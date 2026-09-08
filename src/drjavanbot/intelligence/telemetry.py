from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3
import time

from .models import RequestedFactCoverage, SourceRoute


class IntelligenceTelemetryStore:
    """PII-safe categorical telemetry: never stores question/evidence/prompt text."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS intelligence_events("
                "id INTEGER PRIMARY KEY,created_at REAL NOT NULL,category_hash TEXT NOT NULL,stage TEXT NOT NULL,"
                "success INTEGER NOT NULL,router_sources TEXT,facets TEXT,query_count INTEGER,candidate_count INTEGER,"
                "reranked_count INTEGER,requested_fact_supported INTEGER,answerable INTEGER,model_stage TEXT,"
                "finish_reason TEXT,input_tokens INTEGER,output_tokens INTEGER,latency_ms REAL,fallback_used INTEGER,"
                "cost_irt REAL)"
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_intelligence_events_created ON intelligence_events(created_at)")

    def record(
        self,
        *,
        category: str,
        stage: str,
        success: bool,
        route: SourceRoute | None = None,
        facets: tuple[str, ...] = (),
        query_count: int | None = None,
        candidate_count: int | None = None,
        reranked_count: int | None = None,
        coverage: RequestedFactCoverage | None = None,
        model_stage: str | None = None,
        finish_reason: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: float | None = None,
        fallback_used: bool = False,
        cost_irt: float | None = None,
    ) -> None:
        category_hash = hashlib.sha256(category.encode("utf-8")).hexdigest()[:16]
        sources = tuple(item.source_type for item in route.selected_sources) if route else ()
        with self._connect() as con:
            con.execute(
                "INSERT INTO intelligence_events(created_at,category_hash,stage,success,router_sources,facets,"
                "query_count,candidate_count,reranked_count,requested_fact_supported,answerable,model_stage,finish_reason,"
                "input_tokens,output_tokens,latency_ms,fallback_used,cost_irt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), category_hash, stage, int(success), json.dumps(sources), json.dumps(tuple(facets)),
                    query_count, candidate_count, reranked_count,
                    None if coverage is None else int(coverage.requested_fact_supported),
                    None if coverage is None else int(coverage.answerable), model_stage, finish_reason,
                    input_tokens, output_tokens, latency_ms, int(fallback_used), cost_irt,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA busy_timeout=5000")
        return con


__all__ = ["IntelligenceTelemetryStore"]
