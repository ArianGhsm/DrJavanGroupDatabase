"""Discussion retrieval: find the group conversations that answer a question.

Candidate generation is deliberately recall-oriented (the target discussion is
in the top 30 for ~88% of real questions); precise ordering is left to the
LLM reranker, which reads the candidates. Several query phrasings — the user's
own words plus LLM rewrites in archive vocabulary — are fused with reciprocal
rank fusion, and an optional dense (embedding) index joins the same fusion.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
import math
from pathlib import Path
import sqlite3
from typing import Protocol, Sequence

from drjavanbot.knowledge.store import Card, KnowledgeStore
from drjavanbot.normalization import normalize_text
from drjavanbot.search.lexicon import DentalLexicon
from drjavanbot.search.numerals import colloquial_variants, number_variants
from drjavanbot.search.terms import informative_tokens
from drjavanbot.storage.database import connect_database

_POOL = 300
_RRF_K = 60
# Relative weight of a match in the discussion's opening message versus
# anywhere in it; tuned on the real-question evaluation set.
_ROOT_WEIGHT = 0.5


class DenseIndex(Protocol):
    def search(self, query: str, limit: int) -> Sequence[int]:
        """Return discussion ids ranked by semantic similarity."""


@dataclass(frozen=True, slots=True)
class ArchiveMessage:
    row_id: int
    message_id: int | None
    author: str | None
    datetime: datetime | None
    text: str
    reply_to_message_id: int | None


@dataclass(frozen=True, slots=True)
class Discussion:
    discussion_id: int
    key: str
    messages: tuple[ArchiveMessage, ...]
    header: ArchiveMessage | None  # opening question when this is a later chunk
    card: "Card | None" = None     # LLM study card, when the archive has been studied

    @property
    def start(self) -> datetime | None:
        return next((m.datetime for m in self.messages if m.datetime), None)


class DiscussionRetriever:
    def __init__(
        self,
        db_path: Path,
        *,
        lexicon: DentalLexicon | None = None,
        dense: DenseIndex | None = None,
        knowledge: KnowledgeStore | None = None,
    ) -> None:
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        self.db_path = db_path
        self.lexicon = lexicon or DentalLexicon.load_default()
        self.dense = dense
        self.knowledge = knowledge

    # -- candidates -------------------------------------------------------
    def search(self, queries: Sequence[str], *, limit: int = 40) -> tuple[int, ...]:
        """Fuse lexical (and dense) rankings for every query phrasing."""
        phrasings = tuple(dict.fromkeys(q for q in (normalize_text(v) for v in queries) if q))
        if not phrasings:
            return ()
        connection = connect_database(self.db_path, readonly=True)
        try:
            rankings = [self._lexical(connection, query) for query in phrasings]
            if self.knowledge is not None:
                # Study cards carry the vocabulary users ask with (synonyms,
                # English terms, lay words), bridging paraphrase gaps.
                rankings.extend(self._cards(connection, query) for query in phrasings)
        finally:
            connection.close()
        if self.dense is not None:
            rankings.extend(list(self.dense.search(query, _POOL)) for query in phrasings)
        return tuple(_rrf(rankings)[:limit])

    def _lexical(self, connection: sqlite3.Connection, query: str) -> list[int]:
        tokens = informative_tokens(query)
        if not tokens:
            return []
        alternates = {token: self._alternates(token) for token in tokens}
        expression = " OR ".join(
            "(" + " OR ".join(_quote(value) for value in alternates[token]) + ")" for token in tokens
        )
        try:
            rows = connection.execute(
                "SELECT d.id, d.text_normalized, m.text_normalized FROM discussions_fts f "
                "JOIN discussions d ON d.id=f.rowid JOIN messages m ON m.id=d.root_row_id "
                "WHERE discussions_fts MATCH ? ORDER BY bm25(discussions_fts) LIMIT ?",
                (expression, _POOL),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        weights = _idf_weights(connection, alternates)
        total = sum(weights.values()) or 1.0

        def coverage(text: str) -> float:
            padded = " " + (text or "")
            return sum(weights[t] for t in tokens if any(f" {value}" in padded for value in alternates[t])) / total

        scored = [
            (3.0 * coverage(body) + 3.0 * _ROOT_WEIGHT * coverage(opening) + 5.0 / (rank + 10), discussion_id)
            for rank, (discussion_id, body, opening) in enumerate(rows)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [discussion_id for _, discussion_id in scored]

    def _cards(self, connection: sqlite3.Connection, query: str) -> list[int]:
        tokens = informative_tokens(query)
        if not tokens or self.knowledge is None:
            return []
        expression = " OR ".join(
            "(" + " OR ".join(_quote(value) for value in self._alternates(token)) + ")" for token in tokens
        )
        keys = self.knowledge.search(expression, limit=_POOL)
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        ids = dict(connection.execute(
            f"SELECT discussion_key, id FROM discussions WHERE discussion_key IN ({placeholders})", keys).fetchall())
        return [ids[key] for key in keys if key in ids]

    def _alternates(self, token: str) -> tuple[str, ...]:
        values = [token, *number_variants(token), *colloquial_variants(token)]
        values.extend(s for s in self.lexicon.expand(token, limit=8) if " " not in s and len(s) >= 3)
        return tuple(dict.fromkeys(values))[:8]

    # -- content ----------------------------------------------------------
    def load(self, discussion_ids: Sequence[int]) -> tuple[Discussion, ...]:
        ids = tuple(dict.fromkeys(int(v) for v in discussion_ids))
        if not ids:
            return ()
        connection = connect_database(self.db_path, readonly=True)
        try:
            placeholders = ",".join("?" for _ in ids)
            meta = {row[0]: (row[1], row[2], row[3]) for row in connection.execute(
                f"SELECT id, discussion_key, root_row_id, chunk_index FROM discussions WHERE id IN ({placeholders})", ids)}
            members: dict[int, list[ArchiveMessage]] = defaultdict(list)
            for row in connection.execute(
                "SELECT dm.discussion_id, m.id, m.message_id, m.author, m.datetime_utc, m.text_raw, m.reply_to_message_id "
                f"FROM discussion_messages dm JOIN messages m ON m.id=dm.message_row_id WHERE dm.discussion_id IN ({placeholders}) "
                "ORDER BY dm.discussion_id, dm.position",
                ids,
            ):
                members[row[0]].append(_message(row[1:]))
            out: list[Discussion] = []
            for discussion_id in ids:
                if discussion_id not in meta:
                    continue
                key, root_row_id, chunk_index = meta[discussion_id]
                header = None
                if chunk_index:
                    root = connection.execute(
                        "SELECT id, message_id, author, datetime_utc, text_raw, reply_to_message_id FROM messages WHERE id=?",
                        (root_row_id,),
                    ).fetchone()
                    header = _message(root) if root else None
                out.append(Discussion(discussion_id, key, tuple(members[discussion_id]), header))
        finally:
            connection.close()
        if self.knowledge is not None and out:
            cards = self.knowledge.get_many([d.key for d in out])
            out = [replace(d, card=cards.get(d.key)) for d in out]
        return tuple(out)


def _message(row) -> ArchiveMessage:
    stamp = None
    if row[3]:
        try:
            stamp = datetime.fromisoformat(row[3])
        except ValueError:
            stamp = None
    return ArchiveMessage(int(row[0]), row[1], row[2], stamp, row[4] or "", row[5])


def _idf_weights(connection: sqlite3.Connection, alternates: dict[str, tuple[str, ...]]) -> dict[str, float]:
    total = connection.execute("SELECT count(*) FROM discussions").fetchone()[0] or 1
    weights: dict[str, float] = {}
    for token, values in alternates.items():
        df = 0
        for value in values:
            row = connection.execute("SELECT doc FROM messages_fts_vocab WHERE term=?", (value,)).fetchone()
            if row is not None:
                df = max(df, int(row[0]))
        weights[token] = max(0.3, math.log((total + 1) / (df + 1)))
    return weights


def _rrf(rankings: Sequence[Sequence[int]]) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] += 1.0 / (_RRF_K + rank + 1)
    return [item for item, _ in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))]


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


__all__ = ["ArchiveMessage", "DenseIndex", "Discussion", "DiscussionRetriever"]
