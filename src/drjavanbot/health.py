from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
