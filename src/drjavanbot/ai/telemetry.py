from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

from .models import UsageMetrics


@dataclass(frozen=True, slots=True)
class TelemetrySummary:
    calls: int
    successes: int
    failures: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_irt: float
    average_latency_ms: float


class TelemetryStore:
    """Metadata-only usage log. Prompts, responses, evidence text and secrets are never persisted."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ensure_schema()

    def record(
        self,
        *,
        request_type: str,
        model: str,
        latency_ms: float,
        success: bool,
        usage: UsageMetrics | None = None,
        error_class: str | None = None,
        stage: str | None = None,
        result_class: str | None = None,
        reason_code: str | None = None,
        logical_call: int | None = None,
        evidence_count: int | None = None,
    ) -> None:
        usage = usage or UsageMetrics()
        with self._connect() as con:
            con.execute(
                "INSERT INTO ai_usage(created_at,request_type,model,input_tokens,cached_input_tokens,output_tokens,"
                "cost_irt,cost_unit,exchange_rate,latency_ms,success,error_class,stage,result_class,reason_code,"
                "logical_call,evidence_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), request_type, model, usage.input_tokens, usage.cached_input_tokens,
                    usage.output_tokens, usage.cost_irt, usage.cost_unit, usage.exchange_rate,
                    float(latency_ms), 1 if success else 0, error_class, stage, result_class,
                    reason_code, logical_call, evidence_count,
                ),
            )

    def summary(self) -> TelemetrySummary:
        with self._connect() as con:
            row = con.execute(
                "SELECT count(*),sum(success),sum(CASE WHEN success=0 THEN 1 ELSE 0 END),"
                "coalesce(sum(input_tokens),0),coalesce(sum(cached_input_tokens),0),coalesce(sum(output_tokens),0),"
                "coalesce(sum(cost_irt),0),coalesce(avg(latency_ms),0) FROM ai_usage"
            ).fetchone()
            return TelemetrySummary(
                calls=int(row[0]), successes=int(row[1] or 0), failures=int(row[2] or 0),
                input_tokens=int(row[3]), cached_input_tokens=int(row[4]), output_tokens=int(row[5]),
                cost_irt=float(row[6]), average_latency_ms=float(row[7]),
            )

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS ai_usage("
                "id INTEGER PRIMARY KEY,created_at REAL NOT NULL,request_type TEXT NOT NULL,model TEXT NOT NULL,"
                "input_tokens INTEGER NOT NULL,cached_input_tokens INTEGER NOT NULL,output_tokens INTEGER NOT NULL,"
                "cost_irt REAL,cost_unit TEXT,exchange_rate REAL,latency_ms REAL NOT NULL,success INTEGER NOT NULL,"
                "error_class TEXT,stage TEXT,result_class TEXT,reason_code TEXT,logical_call INTEGER,evidence_count INTEGER)"
            )
            existing = {row[1] for row in con.execute("PRAGMA table_info(ai_usage)")}
            migrations = {
                "stage": "TEXT",
                "result_class": "TEXT",
                "reason_code": "TEXT",
                "logical_call": "INTEGER",
                "evidence_count": "INTEGER",
            }
            for name, sql_type in migrations.items():
                if name not in existing:
                    con.execute(f"ALTER TABLE ai_usage ADD COLUMN {name} {sql_type}")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_created ON ai_usage(created_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_stage ON ai_usage(stage,result_class,reason_code)")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA busy_timeout=5000")
        return con
