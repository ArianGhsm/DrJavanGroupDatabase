from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
from pathlib import Path
import sqlite3
from typing import Iterable, Sequence

from drjavanbot.domain import LinkRef, MediaRef, MessageRecord, MessageType
from drjavanbot.normalization import normalize_author, normalize_text, tokenize
from drjavanbot.storage.database import connect_database, ensure_schema_readonly
from .contracts import EvidenceCandidate, SearchQuery
from .lexicon import DentalLexicon

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
        explicit_variants = tuple(normalize_text(v) for v in query.variants if normalize_text(v))
        synonyms = self.lexicon.expand(normalized)
        variants = _unique((normalized, *explicit_variants, *synonyms))

        scores: dict[int, float] = defaultdict(float)
        reasons: dict[int, set[str]] = defaultdict(set)
        matched: dict[int, set[str]] = defaultdict(set)
        filters, params = _filters(query)

        # A. exact normalized phrase via FTS phrase query, then verified by substring.
        for row in _fts(connection, _phrase(normalized), filters, params, candidate_limit):
            if normalized in (row["text_normalized"] or ""):
                rowid = int(row["id"])
                scores[rowid] += 6.0
                reasons[rowid].add("exact_phrase")
                matched[rowid].add(normalized)

        # B/C. Primary tokens use AND/OR; lexical expansions stay phrase-level
        # so variants such as "ان پی جی" never leak generic single tokens.
        primary_tokens = tokenize(normalized)
        if primary_tokens:
            and_expr = " AND ".join(_quote_fts(token) for token in primary_tokens)
            for row in _fts(connection, and_expr, filters, params, candidate_limit):
                rowid = int(row["id"])
                scores[rowid] += 3.0
                reasons[rowid].add("normalized_tokens")
                text = row["text_normalized"] or ""
                matched[rowid].update(token for token in primary_tokens if token in text)

        expressions: list[str] = [_quote_fts(token) for token in primary_tokens]
        expressions.extend(_quote_fts(variant) for variant in variants[1:] if variant)
        if expressions:
            or_expr = " OR ".join(expressions[:64])
            for row in _fts(connection, or_expr, filters, params, candidate_limit):
                rowid = int(row["id"])
                rank = float(row["rank"] or 0.0)
                scores[rowid] += 1.5 + min(1.5, abs(rank))
                reasons[rowid].add("fts_bm25")
                text = row["text_normalized"] or ""
                matched[rowid].update(token for token in primary_tokens if token in text)
                synonym_hits = [term for term in synonyms if term and term in text]
                if synonym_hits:
                    scores[rowid] += 0.8
                    reasons[rowid].add("synonym")
                    matched[rowid].update(synonym_hits)

        # D. bounded typo correction only for primary tokens that lexical search
        # did not actually cover. Running vocabulary similarity for already-hit
        # common terms adds latency but almost no recall; a missing typo token is
        # still expanded even when another token in the same query had matches.
        covered_primary = {
            token
            for terms in matched.values()
            for token in primary_tokens
            if token in terms
        }
        fuzzy_terms: list[tuple[str, float]] = []
        for token in primary_tokens:
            if token not in covered_primary:
                fuzzy_terms.extend(_fuzzy_expansions(connection, token))
        fuzzy_terms = list(dict.fromkeys(fuzzy_terms))[:16]
        if fuzzy_terms:
            fuzzy_expr = " OR ".join(_quote_fts(term) for term, _ in fuzzy_terms)
            similarity_by_term = dict(fuzzy_terms)
            for row in _fts(connection, fuzzy_expr, filters, params, candidate_limit):
                rowid = int(row["id"])
                text = row["text_normalized"] or ""
                hit_terms = [term for term, _ in fuzzy_terms if term in text]
                if hit_terms:
                    best = max(similarity_by_term[term] for term in hit_terms)
                    scores[rowid] += 1.6 * best
                    reasons[rowid].add("fuzzy")
                    matched[rowid].update(hit_terms)

        ranked_ids = [
            rowid for rowid, _ in
            sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:candidate_limit]
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
