from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time

from drjavanbot.ai.models import AnswerResult
from .models import FreshnessClass, QuestionUnderstanding, SourceRoute, SourceType

CACHE_CONTRACT_VERSION = "intelligence-cache-v2.1"
GROUNDING_VERSION = "multisource-grounding-v2.1"
FUSION_VERSION = "evidence-fusion-v2.0"
ROUTER_VERSION = "source-router-v2.2"
RETRIEVAL_VERSION = "multisource-retrieval-v2.1"


class SourceAwareResponseCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._ensure_schema()

    def get(self, key: str) -> AnswerResult | None:
        now = time.time()
        with self._connect() as con:
            row = con.execute("SELECT payload_json,expires_at FROM intelligence_cache WHERE cache_key=?", (key,)).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                con.execute("DELETE FROM intelligence_cache WHERE cache_key=?", (key,)); return None
            try:
                payload = json.loads(row["payload_json"])
                if not isinstance(payload, dict): raise ValueError
                return AnswerResult.from_dict(payload).with_runtime(cache_hit=True, ai_calls=0)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                con.execute("DELETE FROM intelligence_cache WHERE cache_key=?", (key,)); return None

    def set(self, key: str, answer: AnswerResult, *, ttl_seconds: int) -> None:
        now = time.time()
        payload = json.dumps(answer.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as con:
            con.execute(
                "INSERT INTO intelligence_cache(cache_key,payload_json,created_at,expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json,created_at=excluded.created_at,expires_at=excluded.expires_at",
                (key, payload, now, now + max(60, int(ttl_seconds))),
            )

    def clear(self) -> int:
        with self._connect() as con:
            count = int(con.execute("SELECT count(*) FROM intelligence_cache").fetchone()[0])
            con.execute("DELETE FROM intelligence_cache"); return count

    def stats(self) -> dict[str, int]:
        now = time.time()
        with self._connect() as con:
            entries = int(con.execute("SELECT count(*) FROM intelligence_cache").fetchone()[0])
            expired = int(con.execute("SELECT count(*) FROM intelligence_cache WHERE expires_at<=?", (now,)).fetchone()[0])
            return {"entries": entries, "expired_entries": expired}

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS intelligence_cache(cache_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_intelligence_cache_expiry ON intelligence_cache(expires_at)")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5); con.row_factory = sqlite3.Row; con.execute("PRAGMA busy_timeout=5000"); return con


def cache_key(understanding: QuestionUnderstanding, route: SourceRoute, *, model_signature: str, archive_fingerprint: str = "") -> str:
    payload = {
        "cache": CACHE_CONTRACT_VERSION, "grounding": GROUNDING_VERSION, "fusion": FUSION_VERSION, "router": ROUTER_VERSION,
        "retrieval": RETRIEVAL_VERSION,
        "question_schema": understanding.schema_version, "question": understanding.normalized_question,
        "facets": list(understanding.facets), "entities": [item.canonical_id for item in understanding.entities],
        "freshness": understanding.freshness, "geography": understanding.geography.country_code,
        "route": [(item.source_type, item.requirement, item.freshness_requirement) for item in route.selected_sources],
        "model": model_signature, "archive": archive_fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ttl_for_route(understanding: QuestionUnderstanding, route: SourceRoute) -> int:
    required = set(route.required_sources)
    if understanding.freshness == FreshnessClass.REALTIME:
        return 15 * 60
    if understanding.current_information_needed or required & {SourceType.CURRENT_WEB, SourceType.OFFICIAL}:
        return 2 * 3600
    if required == {SourceType.ARCHIVE}:
        return 7 * 24 * 3600
    if SourceType.SCIENTIFIC in required:
        return 3 * 24 * 3600
    return 12 * 3600


__all__ = ["SourceAwareResponseCache", "cache_key", "ttl_for_route", "CACHE_CONTRACT_VERSION", "GROUNDING_VERSION", "FUSION_VERSION", "ROUTER_VERSION", "RETRIEVAL_VERSION"]
