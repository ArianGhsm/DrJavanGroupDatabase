from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

from .planner import SearchPlan


class SearchPlanCache:
    """Small resilient SQLite cache for semantic search plans.

    The caller includes index fingerprint and planner version in the key, so an
    archive/index change invalidates old plans without destructive migrations.
    """

    def __init__(self, path: Path, *, ttl_seconds: int = 86400) -> None:
        self.path = path
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS search_plan_cache("
                    "cache_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,expires_at REAL NOT NULL,created_at REAL NOT NULL)"
                )
        except sqlite3.Error:
            # Cache failure must not stop grounded answering.
            pass

    def get(self, key: str, *, question: str) -> SearchPlan | None:
        try:
            with self._connect() as con:
                row = con.execute(
                    "SELECT payload_json,expires_at FROM search_plan_cache WHERE cache_key=?", (key,)
                ).fetchone()
                if row is None:
                    return None
                if float(row["expires_at"]) <= time.time():
                    con.execute("DELETE FROM search_plan_cache WHERE cache_key=?", (key,))
                    return None
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, dict):
                    raise ValueError
                return SearchPlan.from_dict(payload, question=question)
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError, OSError):
            try:
                with self._connect() as con:
                    con.execute("DELETE FROM search_plan_cache WHERE cache_key=?", (key,))
            except sqlite3.Error:
                pass
            return None

    def set(self, key: str, plan: SearchPlan) -> None:
        now = time.time()
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO search_plan_cache(cache_key,payload_json,expires_at,created_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json,"
                    "expires_at=excluded.expires_at,created_at=excluded.created_at",
                    (key, json.dumps(plan.to_dict(), ensure_ascii=False, separators=(",", ":")), now + self.ttl_seconds, now),
                )
        except sqlite3.Error:
            pass

    def clear(self) -> int:
        try:
            with self._connect() as con:
                count = int(con.execute("SELECT count(*) FROM search_plan_cache").fetchone()[0])
                con.execute("DELETE FROM search_plan_cache")
                return count
        except sqlite3.Error:
            return 0

    def stats(self) -> dict[str, int]:
        try:
            with self._connect() as con:
                now = time.time()
                con.execute("DELETE FROM search_plan_cache WHERE expires_at<=?", (now,))
                return {"entries": int(con.execute("SELECT count(*) FROM search_plan_cache").fetchone()[0])}
        except sqlite3.Error:
            return {"entries": 0}

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con


__all__ = ["SearchPlanCache"]
