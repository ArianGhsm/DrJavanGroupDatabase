"""Derived knowledge: LLM "study cards" for group discussions.

Cards live in their own SQLite file, not in the archive index, because the
index is rebuilt atomically on every full reindex. A card is keyed by the
discussion's stable key and remembers the content hash it was written for,
so only new or changed discussions are studied again.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
from typing import Sequence

from drjavanbot.normalization import normalize_text

CARD_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Card:
    discussion_key: str
    content_hash: str
    topic: str
    question: str
    summary: str
    keywords: tuple[str, ...]
    javan_view: str | None
    useful: bool
    payload: dict

    @property
    def search_text(self) -> str:
        return normalize_text(" ".join((self.topic, self.question, self.summary, " ".join(self.keywords), self.javan_view or "")))


class KnowledgeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS cards(
                    discussion_key TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    useful INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    model TEXT,
                    created_at REAL NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
                    discussion_key UNINDEXED, search_text, tokenize='unicode61 remove_diacritics 2'
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def known_hashes(self) -> dict[str, str]:
        with self._connect() as con:
            return dict(con.execute("SELECT discussion_key, content_hash FROM cards WHERE schema_version=?", (CARD_SCHEMA_VERSION,)))

    def put(self, card: Card, *, model: str | None = None) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO cards(discussion_key,content_hash,schema_version,topic,useful,payload_json,model,created_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(discussion_key) DO UPDATE SET content_hash=excluded.content_hash,"
                "schema_version=excluded.schema_version,topic=excluded.topic,useful=excluded.useful,"
                "payload_json=excluded.payload_json,model=excluded.model,created_at=excluded.created_at",
                (card.discussion_key, card.content_hash, CARD_SCHEMA_VERSION, card.topic, int(card.useful),
                 json.dumps(card.payload, ensure_ascii=False), model, time.time()),
            )
            con.execute("DELETE FROM cards_fts WHERE discussion_key=?", (card.discussion_key,))
            if card.useful:
                con.execute("INSERT INTO cards_fts(discussion_key, search_text) VALUES(?,?)", (card.discussion_key, card.search_text))

    def get_many(self, keys: Sequence[str]) -> dict[str, Card]:
        keys = tuple(dict.fromkeys(keys))
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT discussion_key, content_hash, payload_json FROM cards WHERE discussion_key IN ({placeholders})", keys,
            ).fetchall()
        return {row[0]: card_from_payload(row[0], row[1], json.loads(row[2])) for row in rows}

    def search(self, expression: str, *, limit: int = 300) -> list[str]:
        """Discussion keys whose card matches an FTS5 expression, best first."""
        try:
            with self._connect() as con:
                return [row[0] for row in con.execute(
                    "SELECT discussion_key FROM cards_fts WHERE cards_fts MATCH ? ORDER BY bm25(cards_fts) LIMIT ?",
                    (expression, limit),
                )]
        except sqlite3.OperationalError:
            return []

    def stats(self) -> dict[str, int]:
        with self._connect() as con:
            total, useful = con.execute("SELECT count(*), coalesce(sum(useful),0) FROM cards").fetchone()
        return {"cards": int(total), "useful": int(useful)}


def card_from_payload(key: str, content_hash: str, payload: dict) -> Card:
    def text(name: str) -> str:
        value = payload.get(name)
        return " ".join(value.split()) if isinstance(value, str) else ""

    javan = payload.get("javan_view")
    javan_text = " ".join(javan["text"].split()) if isinstance(javan, dict) and isinstance(javan.get("text"), str) else None
    keywords = tuple(k.strip() for k in payload.get("keywords") or () if isinstance(k, str) and k.strip())[:24]
    return Card(key, content_hash, text("topic"), text("question"), text("summary"), keywords, javan_text or None,
                bool(payload.get("useful", True)), payload)


__all__ = ["CARD_SCHEMA_VERSION", "Card", "KnowledgeStore", "card_from_payload"]
