from __future__ import annotations

from dataclasses import dataclass
import os

ALLOWED_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    poll_timeout_seconds: int = 25
    worker_count: int = 4
    max_question_chars: int = 2000
    default_rate_limit_per_minute: int = 6
    key_entry_timeout_seconds: int = 300
    source_session_ttl_seconds: int = 3600
    update_check_interval_seconds: int = 300
    rich_ui_enabled: bool = True
    progress_ui_enabled: bool = True

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        return cls(
            poll_timeout_seconds=_int("DRJAVAN_TG_POLL_TIMEOUT_SECONDS", 25, 5, 50),
            worker_count=_int("DRJAVAN_TG_WORKERS", 4, 1, 16),
            max_question_chars=_int("DRJAVAN_TG_MAX_QUESTION_CHARS", 2000, 200, 8000),
            default_rate_limit_per_minute=_int("DRJAVAN_TG_RATE_LIMIT_PER_MINUTE", 6, 1, 60),
            key_entry_timeout_seconds=_int("DRJAVAN_TG_KEY_ENTRY_TIMEOUT_SECONDS", 300, 60, 1800),
            source_session_ttl_seconds=_int("DRJAVAN_TG_SOURCE_SESSION_TTL_SECONDS", 3600, 300, 86400),
            update_check_interval_seconds=_int("DRJAVAN_TG_UPDATE_CHECK_INTERVAL_SECONDS", 300, 60, 3600),
            rich_ui_enabled=_bool("DRJAVAN_TG_RICH_UI_ENABLED", True),
            progress_ui_enabled=_bool("DRJAVAN_TG_PROGRESS_UI_ENABLED", True),
        )


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
