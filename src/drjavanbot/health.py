from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from drjavanbot.storage import database_health


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class HealthReport:
    state: HealthState
    index_ready: bool
    ai_configured: bool
    archive_files: int | None = None
    indexed_messages: int | None = None
    index_version: str | None = None
    detail: str | None = None


def local_index_health(db_path: Path) -> HealthReport:
    data = database_health(db_path)
    ready = bool(data.get("healthy"))
    return HealthReport(
        state=HealthState.HEALTHY if ready else HealthState.UNHEALTHY,
        index_ready=ready,
        ai_configured=False,
        archive_files=int(data["archive_files"]) if data.get("archive_files") is not None else None,
        indexed_messages=int(data["messages"]) if data.get("messages") is not None else None,
        index_version=str(data["schema_version"]) if data.get("schema_version") is not None else None,
        detail=str(data.get("detail") or ""),
    )
