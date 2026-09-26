from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import math
import re
from pathlib import Path
import sqlite3
from typing import Iterable, Sequence

from drjavanbot.domain import LinkRef, MediaRef, MessageRecord, MessageType
from drjavanbot.normalization import normalize_author, normalize_text, tokenize
from drjavanbot.storage.database import connect_database, ensure_schema_readonly
from .contracts import EvidenceCandidate, SearchQuery
from .lexicon import DentalLexicon
from .numerals import colloquial_variants, number_variants
from .terms import informative_query

_LOW_INFORMATION = {
    "بله", "نه", "آره", "اره", "خوبه", "خوب بود", "همینه", "همین", "ممنون",
    "yes", "no", "ok", "okay", "thanks", "thank you",
}


class SQLiteSearchBackend:
    """Bounded local retrieval over the canonical SQLite/FTS5 index."""

    def __init__(self, db_path: Path, *, lexicon: DentalLexicon | None = None) -> None:
        self.db_path = db_path
        self.lexicon = lexicon or DentalLexicon.load_default()
        if not db_path.exists():
            raise FileNotFoundError(db_path)

    def search(self, query: SearchQuery) -> tuple[EvidenceCandidate, ...]:
        connection = connect_database(self.db_path, readonly=True)
        try:
            ensure_schema_readonly(connection)
            return self._search_with_connection(connection, query)
        finally:
            connection.close()

    def search_many(self, queries: Sequence[SearchQuery]) -> tuple[tuple[EvidenceCandidate, ...], ...]:
        """Execute related query families on one read connection.

        Semantic planning commonly emits several short query families. Reusing
        the same SQLite connection preserves its page cache and avoids repeated
        connection/schema setup while keeping every query independently scored.
        """
        query_tuple = tuple(queries)
        if not query_tuple:
            return ()
        connection = connect_database(self.db_path, readonly=True)
        try:
            ensure_schema_readonly(connection)
            return tuple(self._search_with_connection(connection, query) for query in query_tuple)
        finally:
            connection.close()

    def _search_with_connection(
        self,
        connection: sqlite3.Connection,
        query: SearchQuery,
    ) -> tuple[EvidenceCandidate, ...]:
        normalized = normalize_text(query.normalized_query or query.raw_query)
        if not normalized:
            return ()
        candidate_limit = max(1, min(query.candidate_limit, 500))
        evidence_limit = max(1, min(query.evidence_limit, candidate_limit, 100))
        # Conversational filler ("کسی میدونه ... چنده") never gates retrieval;
        # a query made only of filler still searches its raw tokens.
        topical = informative_query(normalized)
        if topical:
            normalized = topical
        explicit_variants = tuple(normalize_text(v) for v in query.variants if normalize_text(v))
        synonyms = self.lexicon.expand(normalized)
        variants = _unique((normalized, *explicit_variants, *synonyms))

        scores: dict[int, float] = defaultdict(float)
        reasons: dict[int, set[str]] = defaultdict(set)
        matched: dict[int, set[str]] = defaultdict(set)
        texts: dict[int, str] = {}
        bm25: dict[int, float] = {}
        filters, params = _filters(query)

        def seen(row: sqlite3.Row) -> int:
            rowid = int(row["id"])
            texts[rowid] = row["text_normalized"] or ""
            rank = float(row["rank"] or 0.0)
            if rowid not in bm25 or rank < bm25[rowid]:
                bm25[rowid] = rank
            return rowid

        # A. exact normalized phrase via FTS phrase query, then verified by substring.
        for row in _fts(connection, _phrase(normalized), filters, params, candidate_limit):
            if normalized in (row["text_normalized"] or ""):
                rowid = seen(row)
                scores[rowid] += 6.0
                reasons[rowid].add("exact_phrase")
                matched[rowid].add(normalized)

        # Every primary token may be satisfied by an equivalent surface form:
        # numerals ("هفتم" ↔ "7") and single-token lexicon synonyms.
        primary_tokens = tokenize(normalized)
        alternates = {token: self._token_alternates(token) for token in primary_tokens}
        weights = _token_weights(connection, alternates)

        # B. all tokens (each as an OR group of its equivalents).
        if primary_tokens:
            and_expr = " AND ".join(_group_expr(alternates[token]) for token in primary_tokens)
            for row in _fts(connection, and_expr, filters, params, candidate_limit):
                rowid = seen(row)
                scores[rowid] += 3.0
                reasons[rowid].add("normalized_tokens")

        # B2. drop-one conjunctions keep messages that miss a single (often
        # misspelled or paraphrased) token ahead of messages matching just one.
        if 3 <= len(primary_tokens) <= 6:
            for skip in primary_tokens:
                expr = " AND ".join(_group_expr(alternates[t]) for t in primary_tokens if t != skip)
                for row in _fts(connection, expr, filters, params, max(20, candidate_limit // 2)):
                    rowid = seen(row)
                    reasons[rowid].add("partial_tokens")

        # C. broad recall: any token equivalent or multi-word lexical variant.
        expressions: list[str] = []
        for token in primary_tokens:
            expressions.extend(_quote_fts(value) for value in alternates[token])
        expressions.extend(_quote_fts(variant) for variant in variants[1:] if variant)
        expressions = list(dict.fromkeys(expressions))
        if expressions:
            or_expr = " OR ".join(expressions[:64])
            for row in _fts(connection, or_expr, filters, params, candidate_limit):
                rowid = seen(row)
                reasons[rowid].add("fts_bm25")
                synonym_hits = [term for term in synonyms if term and _has_term(texts[rowid], term)]
                if synonym_hits:
                    scores[rowid] += 0.5
                    reasons[rowid].add("synonym")
                    matched[rowid].update(synonym_hits)

        # D. bounded typo correction only for primary tokens that lexical search
        # did not actually cover. Running vocabulary similarity for already-hit
        # common terms adds latency but almost no recall; a missing typo token is
        # still expanded even when another token in the same query had matches.
        covered_primary = {
            token
            for text in texts.values()
            for token in primary_tokens
            if any(_has_term(text, value) for value in alternates[token])
        }
        fuzzy_terms: list[tuple[str, float]] = []
        for token in primary_tokens:
            if token not in covered_primary:
                fuzzy_terms.extend(_fuzzy_expansions(connection, token))
        fuzzy_terms = list(dict.fromkeys(fuzzy_terms))[:16]
        fuzzy_bonus: dict[int, float] = {}
        if fuzzy_terms:
            fuzzy_expr = " OR ".join(_quote_fts(term) for term, _ in fuzzy_terms)
            similarity_by_term = dict(fuzzy_terms)
            for row in _fts(connection, fuzzy_expr, filters, params, candidate_limit):
                rowid = seen(row)
                hit_terms = [term for term, _ in fuzzy_terms if _has_term(texts[rowid], term)]
                if hit_terms:
                    fuzzy_bonus[rowid] = max(similarity_by_term[term] for term in hit_terms)
                    reasons[rowid].add("fuzzy")
                    matched[rowid].update(hit_terms)

        # Relevance = IDF-weighted share of the query actually present in the
        # message (dominant), plus a per-query-normalized BM25 tie-breaker.
        # Previously BM25 saturated to a constant, so ties fell back to row id
        # and the *oldest* message containing any single word ranked first.
        best_rank = min(bm25.values(), default=0.0)
        total_weight = sum(weights.values()) or 1.0
        for rowid, text in texts.items():
            covered = 0.0
            for token in primary_tokens:
                if _has_term(text, token):
                    covered += weights[token]
                    matched[rowid].add(token)
                else:
                    hit = next((value for value in alternates[token][1:] if _has_term(text, value)), None)
                    if hit is not None:
                        covered += 0.85 * weights[token]
                        matched[rowid].add(hit)
            coverage = covered / total_weight
            if rowid in fuzzy_bonus:
                coverage = max(coverage, min(1.0, coverage + 0.6 * fuzzy_bonus[rowid] / max(1, len(primary_tokens))))
            relative_rank = (bm25[rowid] / best_rank) if best_rank < 0 else 0.0
            scores[rowid] += 6.0 * coverage + 1.0 * max(0.0, min(1.0, relative_rank)) + _substance(text)
            if coverage >= 0.999 and "normalized_tokens" not in reasons[rowid] and len(primary_tokens) > 1:
                reasons[rowid].add("normalized_tokens")

        ranked_ids = [
            rowid for rowid, _ in
            sorted(scores.items(), key=lambda item: (-round(item[1], 6), bm25.get(item[0], 0.0), item[0]))[:candidate_limit]
        ]
        records = _load_records(connection, ranked_ids)
        ordered = [records[rowid] for rowid in ranked_ids if rowid in records]

        cluster_counts: dict[str, int] = defaultdict(int)
        for record in ordered:
            cluster_counts[_cluster_key(record)] += 1

        output: list[EvidenceCandidate] = []
        seen_author_cluster: dict[tuple[str, str | None], MessageRecord] = {}
        for rowid in ranked_ids:
            record = records.get(rowid)
            if record is None:
                continue
            cluster = _cluster_key(record)
            author_cluster = (cluster, record.author_normalized)
            first = seen_author_cluster.get(author_cluster)
            duplicate_of = first.message_id if first is not None else None
            if first is not None:
                continue
            seen_author_cluster[author_cluster] = record

            context: tuple[MessageRecord, ...] = ()
            if query.include_context:
                before, after = _adaptive_context(record, query)
                context = tuple(self._get_context_with_connection(
                    connection,
                    record,
                    before=before,
                    after=after,
                    follow_reply=True,
                    reply_depth=max(0, min(query.reply_depth, 6)),
                ))
            output.append(EvidenceCandidate(
                message=record,
                local_score=round(scores[rowid], 6),
                matched_terms=tuple(sorted(matched[rowid])),
                match_reasons=tuple(sorted(reasons[rowid])),
                context=context,
                cluster_key=cluster,
                cluster_size=cluster_counts[cluster],
                duplicate_of=duplicate_of,
            ))
            if len(output) >= evidence_limit:
                break
        return tuple(output)

    def _token_alternates(self, token: str) -> tuple[str, ...]:
        values = [token, *number_variants(token), *colloquial_variants(token)]
        for synonym in self.lexicon.expand(token, limit=8):
            if " " not in synonym and len(synonym) >= 3:
                values.append(synonym)
        return tuple(dict.fromkeys(values))[:8]

    def hydrate_context(
        self,
        candidates: Sequence[EvidenceCandidate],
        *,
        reply_depth: int = 4,
        limit: int = 24,
    ) -> tuple[EvidenceCandidate, ...]:
        """Hydrate context only for fused winners using one read connection."""
        values = tuple(candidates)
        if not values:
            return ()
        bounded_limit = max(0, min(int(limit), len(values), 40))
        bounded_depth = max(0, min(int(reply_depth), 6))
        connection = connect_database(self.db_path, readonly=True)
        try:
            ensure_schema_readonly(connection)
            hydrated: list[EvidenceCandidate] = []
            for index, candidate in enumerate(values):
                if index >= bounded_limit:
                    hydrated.append(candidate)
                    continue
                query = SearchQuery(
                    raw_query=candidate.message.text_normalized or candidate.message.text_raw,
                    reply_depth=bounded_depth,
                )
                before, after = _adaptive_context(candidate.message, query)
                context = tuple(self._get_context_with_connection(
                    connection,
                    candidate.message,
                    before=before,
                    after=after,
                    follow_reply=True,
                    reply_depth=bounded_depth,
                ))
                reasons = set(candidate.match_reasons)
                score = candidate.local_score
                if context:
                    reasons.add("context_available")
                    score += 0.20
                hydrated.append(replace(
                    candidate,
                    local_score=round(score, 6),
                    match_reasons=tuple(sorted(reasons)),
                    context=context,
                ))
            return tuple(hydrated)
        finally:
            connection.close()

    def get_message(self, message_id: int) -> MessageRecord | None:
        connection = connect_database(self.db_path, readonly=True)
        try:
            row = connection.execute(
                "SELECT id FROM messages WHERE message_id=? ORDER BY source_page, source_order LIMIT 1",
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            return _load_records(connection, [int(row["id"])])[int(row["id"])]
        finally:
            connection.close()

    def get_context(
        self,
        message: MessageRecord,
        *,
        before: int = 2,
        after: int = 3,
        follow_reply: bool = True,
    ) -> tuple[MessageRecord, ...]:
        connection = connect_database(self.db_path, readonly=True)
        try:
            return tuple(self._get_context_with_connection(
                connection,
                message,
                before=max(0, before),
                after=max(0, after),
                follow_reply=follow_reply,
                reply_depth=3,
            ))
        finally:
            connection.close()

    def _get_context_with_connection(
        self,
        connection: sqlite3.Connection,
        message: MessageRecord,
        *,
        before: int,
        after: int,
        follow_reply: bool,
        reply_depth: int,
    ) -> list[MessageRecord]:
        ids: list[int] = []
        if follow_reply and message.reply_to_message_id is not None:
            target = message.reply_to_message_id
            seen_targets: set[int] = set()
            for _ in range(reply_depth):
                if target in seen_targets:
                    break
                seen_targets.add(target)
                row = connection.execute(
                    "SELECT id, reply_to_message_id FROM messages WHERE message_id=? ORDER BY source_page, source_order LIMIT 1",
                    (target,),
                ).fetchone()
                if row is None:
                    break
                ids.append(int(row["id"]))
                target = row["reply_to_message_id"]
                if target is None:
                    break

        if before:
            rows = connection.execute(
                """
                SELECT id FROM messages
                WHERE is_service=0 AND (source_page < ? OR (source_page=? AND source_order < ?))
                ORDER BY source_page DESC, source_order DESC LIMIT ?
                """,
                (message.source_page, message.source_page, message.source_order, before),
            ).fetchall()
            ids.extend(reversed([int(row["id"]) for row in rows]))
        if after:
            rows = connection.execute(
                """
                SELECT id FROM messages
                WHERE is_service=0 AND (source_page > ? OR (source_page=? AND source_order > ?))
                ORDER BY source_page, source_order LIMIT ?
                """,
                (message.source_page, message.source_page, message.source_order, after),
            ).fetchall()
            ids.extend(int(row["id"]) for row in rows)

        records = _load_records(connection, _unique(ids))
        out: list[MessageRecord] = []
        seen_locator = {message.source_locator}
        seen_content: set[tuple[str, str | None]] = set()
        for rowid in ids:
            record = records.get(rowid)
            if record is None or record.source_locator in seen_locator:
                continue
            content_key = (record.text_normalized, record.author_normalized)
            if record.text_normalized and content_key in seen_content:
                continue
            seen_locator.add(record.source_locator)
            if record.text_normalized:
                seen_content.add(content_key)
            out.append(record)
            if len(out) >= before + after + reply_depth:
                break
        return out

    def stats(self) -> dict[str, int | str | float | None]:
        connection = connect_database(self.db_path, readonly=True)
        try:
            schema = ensure_schema_readonly(connection)
            files = connection.execute("SELECT count(*) FROM archive_files").fetchone()[0]
            messages = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
            service = connection.execute("SELECT count(*) FROM messages WHERE is_service=1").fetchone()[0]
            authors = connection.execute(
                "SELECT count(DISTINCT author_normalized) FROM messages WHERE author_normalized IS NOT NULL"
            ).fetchone()[0]
            first_dt, last_dt = connection.execute(
                "SELECT min(datetime_utc), max(datetime_utc) FROM messages WHERE datetime_utc IS NOT NULL"
            ).fetchone()
            return {
                "schema_version": schema,
                "archive_files": files,
                "messages": messages,
                "service_messages": service,
                "content_messages": messages - service,
                "authors": authors,
                "first_datetime_utc": first_dt,
                "last_datetime_utc": last_dt,
                "db_size_bytes": self.db_path.stat().st_size,
                "lexicon_version": self.lexicon.version,
            }
        finally:
            connection.close()


def _fts(
    connection: sqlite3.Connection,
    expression: str,
    filters: str,
    params: tuple[object, ...],
    limit: int,
) -> list[sqlite3.Row]:
    if not expression:
        return []
    sql = (
        "SELECT m.id, m.text_normalized, bm25(messages_fts) AS rank "
        "FROM messages_fts JOIN messages m ON m.id=messages_fts.rowid "
        f"WHERE messages_fts MATCH ? {filters} ORDER BY rank LIMIT ?"
    )
    try:
        return connection.execute(sql, (expression, *params, limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def _filters(query: SearchQuery) -> tuple[str, tuple[object, ...]]:
    clauses = ["m.is_service=0"]
    params: list[object] = []
    if query.author:
        clauses.append("m.author_normalized LIKE ?")
        params.append(f"%{normalize_author(query.author) or ''}%")
    if query.date_from:
        clauses.append("m.datetime_utc >= ?")
        params.append(_utc_iso(query.date_from))
    if query.date_to:
        clauses.append("m.datetime_utc <= ?")
        params.append(_utc_iso(query.date_to))
    return " AND " + " AND ".join(clauses), tuple(params)


_URL_RE = re.compile(r"(?:https?|www)\S*|\S+\.(?:com|ir|org|net|io)\S*")


def _substance(text: str) -> float:
    """Prefer messages that say something over one-word echoes and bare links.

    BM25 favours very short documents, so "ارتودنسی" or a lone URL otherwise
    outranks an actual explanation containing the same word. Bounded to 0..1.2
    so it only reorders messages of similar relevance.
    """
    words = [token for token in _URL_RE.sub(" ", text).split() if len(token) > 1]
    return 1.2 * min(1.0, len(words) / 18.0)


def _group_expr(values: Sequence[str]) -> str:
    quoted = [_quote_fts(value) for value in values if value]
    if len(quoted) == 1:
        return quoted[0]
    return "(" + " OR ".join(quoted) + ")"


def _has_term(text: str, term: str) -> bool:
    """Word-prefix match: "ایمپلنت" matches "ایمپلنتها" but "کم" not "مکمل"."""
    if not term or not text:
        return False
    padded = f" {text}"
    return f" {term}" in padded


def _token_weights(connection: sqlite3.Connection, alternates: dict[str, tuple[str, ...]]) -> dict[str, float]:
    """Inverse document frequency per token, using its most common equivalent."""
    if not alternates:
        return {}
    total = _document_count(connection)
    out: dict[str, float] = {}
    for token, values in alternates.items():
        df = 0
        for value in values:
            row = connection.execute("SELECT doc FROM messages_fts_vocab WHERE term=?", (value,)).fetchone()
            if row is not None:
                df = max(df, int(row["doc"]))
        out[token] = max(0.3, math.log((total + 1) / (df + 1)))
    return out


_DOC_COUNT_CACHE: dict[tuple[str, int], int] = {}


def _document_count(connection: sqlite3.Connection) -> int:
    try:
        path = connection.execute("PRAGMA database_list").fetchone()[2] or ""
        version = int(connection.execute("PRAGMA data_version").fetchone()[0])
        mtime = Path(path).stat().st_mtime_ns if path else 0
    except (sqlite3.Error, OSError, TypeError, IndexError):
        path, version, mtime = "", 0, 0
    key = (path, mtime + version)
    cached = _DOC_COUNT_CACHE.get(key)
    if cached is None:
        cached = int(connection.execute("SELECT count(*) FROM messages").fetchone()[0])
        if path:
            _DOC_COUNT_CACHE.clear()
            _DOC_COUNT_CACHE[key] = cached
    return max(1, cached)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _phrase(value: str) -> str:
    return _quote_fts(value)


def _quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _fuzzy_expansions(connection: sqlite3.Connection, token: str) -> list[tuple[str, float]]:
    if len(token) < 4:
        return []
    delta = 2 if len(token) >= 7 else 1
    first = token[0]
    rows = connection.execute(
        "SELECT term, doc FROM messages_fts_vocab WHERE length(term) BETWEEN ? AND ? AND term GLOB ? "
        "ORDER BY doc DESC LIMIT 300",
        (max(2, len(token) - delta), len(token) + delta, f"{first}*"),
    ).fetchall()
    threshold = 0.74 if len(token) >= 7 else 0.80
    scored: list[tuple[str, float]] = []
    for row in rows:
        term = str(row["term"])
        if term == token:
            continue
        similarity = SequenceMatcher(None, token, term).ratio()
        if similarity >= threshold:
            scored.append((term, similarity))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:3]


def _load_records(connection: sqlite3.Connection, row_ids: Iterable[int]) -> dict[int, MessageRecord]:
    ids = list(dict.fromkeys(int(x) for x in row_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(f"SELECT * FROM messages WHERE id IN ({placeholders})", ids).fetchall()
    links: dict[int, list[LinkRef]] = defaultdict(list)
    for row in connection.execute(
        f"SELECT * FROM message_links WHERE message_row_id IN ({placeholders}) ORDER BY message_row_id, ordinal",
        ids,
    ):
        links[int(row["message_row_id"])].append(LinkRef(row["href"], row["label"]))
    media: dict[int, list[MediaRef]] = defaultdict(list)
    for row in connection.execute(
        f"SELECT * FROM message_media WHERE message_row_id IN ({placeholders}) ORDER BY message_row_id, ordinal",
        ids,
    ):
        media[int(row["message_row_id"])].append(MediaRef(row["kind"], row["path"], row["label"]))

    out: dict[int, MessageRecord] = {}
    for row in rows:
        rowid = int(row["id"])
        dt = datetime.fromisoformat(row["datetime_utc"]) if row["datetime_utc"] else None
        out[rowid] = MessageRecord(
            message_id=row["message_id"],
            dom_id=row["dom_id"],
            source_file=row["source_file"],
            source_page=int(row["source_page"]),
            source_order=int(row["source_order"]),
            datetime=dt,
            datetime_raw=row["datetime_raw"],
            author=row["author"],
            author_normalized=row["author_normalized"],
            text_raw=row["text_raw"],
            text_normalized=row["text_normalized"],
            reply_to_message_id=row["reply_to_message_id"],
            reply_source_file=row["reply_source_file"],
            forwarded_from=row["forwarded_from"],
            forwarded_datetime_raw=row["forwarded_datetime_raw"],
            links=tuple(links[rowid]),
            media=tuple(media[rowid]),
            message_type=MessageType(row["message_type"]),
            is_service=bool(row["is_service"]),
            is_joined=bool(row["is_joined"]),
            source_locator=row["source_locator"],
            source_sha256=row["source_sha256"],
            content_hash=row["content_hash"],
            ingest_version=row["ingest_version"],
        )
    return out


def _cluster_key(record: MessageRecord) -> str:
    basis = record.text_normalized or record.content_hash
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _adaptive_context(record: MessageRecord, query: SearchQuery) -> tuple[int, int]:
    if query.context_before is not None or query.context_after is not None:
        return max(0, query.context_before or 0), max(0, query.context_after or 0)
    normalized = record.text_normalized
    low = normalized in _LOW_INFORMATION or len(normalized) <= 24 or len(tokenize(normalized)) <= 3
    if low and record.reply_to_message_id is not None:
        return 1, 2
    if low:
        return 2, 2
    if record.reply_to_message_id is not None:
        return 0, 1
    return 1, 1


def _unique(values: Iterable):
    return tuple(dict.fromkeys(values))
