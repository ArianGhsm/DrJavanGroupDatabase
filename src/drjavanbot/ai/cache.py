from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

from .models import AnswerResult


@dataclass(frozen=True, slots=True)
class CacheStats:
    entries: int
    hits: int
    misses: int
    expired_entries: int


class ResponseCache:
    def __init__(self, path: Path, *, ttl_seconds: int) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._ensure_schema()

    def get(self, key: str) -> AnswerResult | None:
        now = time.time()
        with self._connect() as con:
            row = con.execute("SELECT payload_json, expires_at FROM response_cache WHERE cache_key=?", (key,)).fetchone()
            if row is None:
                self._increment_counter(con, "misses")
                return None
            if float(row["expires_at"]) <= now:
                con.execute("DELETE FROM response_cache WHERE cache_key=?", (key,))
                self._increment_counter(con, "misses")
                return None
            con.execute("UPDATE response_cache SET hits=hits+1 WHERE cache_key=?", (key,))
            self._increment_counter(con, "hits")
            payload = json.loads(row["payload_json"])
            return AnswerResult.from_dict(payload).with_runtime(cache_hit=True, ai_calls=0)

    def set(self, key: str, answer: AnswerResult) -> None:
        now = time.time()
        payload = json.dumps(answer.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as con:
            con.execute(
                "INSERT INTO response_cache(cache_key,payload_json,created_at,expires_at,hits) VALUES(?,?,?,?,0) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json,created_at=excluded.created_at,expires_at=excluded.expires_at,hits=0",
                (key, payload, now, now + self.ttl_seconds),
            )

    def clear(self) -> int:
        with self._connect() as con:
            count = int(con.execute("SELECT count(*) FROM response_cache").fetchone()[0])
            con.execute("DELETE FROM response_cache")
            return count

    def prune_expired(self) -> int:
        now = time.time()
        with self._connect() as con:
            rows = int(con.execute("SELECT count(*) FROM response_cache WHERE expires_at<=?", (now,)).fetchone()[0])
            con.execute("DELETE FROM response_cache WHERE expires_at<=?", (now,))
            return rows

    def stats(self) -> CacheStats:
        now = time.time()
        with self._connect() as con:
            entries = con.execute("SELECT count(*) FROM response_cache").fetchone()[0]
            expired = con.execute("SELECT count(*) FROM response_cache WHERE expires_at<=?", (now,)).fetchone()[0]
            counters = {row[0]: int(row[1]) for row in con.execute("SELECT key,value FROM cache_meta")}
            return CacheStats(int(entries), counters.get("hits", 0), counters.get("misses", 0), int(expired))

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS response_cache("
                "cache_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,created_at REAL NOT NULL,"
                "expires_at REAL NOT NULL,hits INTEGER NOT NULL DEFAULT 0)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_response_cache_expiry ON response_cache(expires_at)")
            con.execute("CREATE TABLE IF NOT EXISTS cache_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL DEFAULT 0)")
            con.execute("INSERT OR IGNORE INTO cache_meta(key,value) VALUES('hits',0),('misses',0)")

    @staticmethod
    def _increment_counter(con: sqlite3.Connection, key: str) -> None:
        con.execute("UPDATE cache_meta SET value=value+1 WHERE key=?", (key,))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        return con
