from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_AVALAI_BASE_URL = "https://api.avalai.ir/v1"
DEFAULT_AVALAI_MODEL = "deepseek-v4-flash"


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str | None
    telegram_owner_id: int | None
    avalai_base_url: str
    avalai_model: str
    log_level: str
    archive_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path

    @classmethod
    def from_env(cls, *, require_runtime: bool = False) -> "Settings":
        token = _blank_to_none(os.getenv("TELEGRAM_BOT_TOKEN"))
        owner_raw = _blank_to_none(os.getenv("TELEGRAM_OWNER_ID"))
        owner_id: int | None = None
        if owner_raw is not None:
            try:
                owner_id = int(owner_raw)
            except ValueError as exc:
                raise ConfigurationError("TELEGRAM_OWNER_ID must be a numeric Telegram user ID") from exc
            if owner_id <= 0:
                raise ConfigurationError("TELEGRAM_OWNER_ID must be positive")

        if require_runtime and not token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is required at runtime")
        if require_runtime and owner_id is None:
            raise ConfigurationError("TELEGRAM_OWNER_ID is required at runtime")

        base_url = os.getenv("AVALAI_BASE_URL", DEFAULT_AVALAI_BASE_URL).strip().rstrip("/")
        model = os.getenv("AVALAI_MODEL", DEFAULT_AVALAI_MODEL).strip()
        if not base_url.startswith("https://"):
            raise ConfigurationError("AVALAI_BASE_URL must use HTTPS")
        if not model:
            raise ConfigurationError("AVALAI_MODEL cannot be empty")

        return cls(
            telegram_bot_token=token,
            telegram_owner_id=owner_id,
            avalai_base_url=base_url,
            avalai_model=model,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            archive_dir=Path(os.getenv("DRJAVAN_ARCHIVE_DIR", "گروه دکتر جوان")),
            data_dir=Path(os.getenv("DRJAVAN_DATA_DIR", "runtime/data")),
            cache_dir=Path(os.getenv("DRJAVAN_CACHE_DIR", "runtime/cache")),
            log_dir=Path(os.getenv("DRJAVAN_LOG_DIR", "runtime/logs")),
        )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
