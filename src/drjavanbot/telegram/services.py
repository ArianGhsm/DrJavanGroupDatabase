from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone
import threading

from drjavanbot.ai.cache import ResponseCache
from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.key_manager import AvalAIKeyManager
from drjavanbot.ai.orchestrator import ArchiveAnswerService
from drjavanbot.ai.provider import AuthenticationError, AvalAIClient
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.secrets import AVALAI_API_KEY_SECRET, LocalFileSecretStore
from drjavanbot.storage import database_health, full_reindex
from .config import ALLOWED_MODELS
from .contracts import IndexNotReadyError
from .state import BotStateStore

class RuntimeServices:
    def __init__(self, *, archive_dir: Path, data_dir: Path, cache_dir: Path, secret_dir: Path, base_ai_config: AIConfig, state: BotStateStore) -> None:
        self.archive_dir = archive_dir
        self.db_path = data_dir / "archive.sqlite3"
        self.state = state
        self.base_ai_config = base_ai_config
        self.secret_store = LocalFileSecretStore(secret_dir)
        self.cache = ResponseCache(cache_dir / "ai_responses.sqlite3", ttl_seconds=base_ai_config.cache_ttl_seconds)
        self.telemetry = TelemetryStore(data_dir / "ai_usage.sqlite3")
        self._reindex_lock = threading.Lock()

    def model(self) -> str:
        model = self.state.selected_model(self.base_ai_config.model)
        return model if model in ALLOWED_MODELS else self.base_ai_config.model

    def set_model(self, model: str) -> None:
        if model not in ALLOWED_MODELS: raise ValueError("model is not allowed")
        self.state.set_model(model)

    def ai_configured(self) -> bool:
        return self.secret_store.is_configured(AVALAI_API_KEY_SECRET)

    def _ai_config(self) -> AIConfig:
        return replace(self.base_ai_config, model=self.model())

    def key_manager(self) -> AvalAIKeyManager:
        return AvalAIKeyManager(self.secret_store, AvalAIClient(self._ai_config()))

    def set_api_key(self, candidate: str) -> bool:
        ok = self.key_manager().validate_and_store(candidate)
        if ok: self.state.set_provider_auth_failed(False)
        return ok

    def remove_api_key(self) -> bool:
        removed = self.key_manager().remove()
        self.state.set_provider_auth_failed(False)
        return removed

    def test_ai(self) -> bool:
        key = self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
        if not key: return False
        ok = AvalAIClient(self._ai_config()).validate_api_key(key)
        self.state.set_provider_auth_failed(not ok)
        return ok

    def answer(self, question: str):
        if not self.db_path.exists(): raise IndexNotReadyError("index database does not exist")
        backend = SQLiteSearchBackend(self.db_path)
        service = ArchiveAnswerService(backend=backend, secret_store=self.secret_store, config=self._ai_config(), cache=self.cache, telemetry=self.telemetry)
        try:
            result = service.answer(question)
        except AuthenticationError:
            self.state.set_provider_auth_failed(True)
            raise
        self.state.set_provider_auth_failed(False)
        return result

    def source_details(self, message_ids: tuple[int, ...], source_refs: tuple[str, ...]) -> list[dict]:
        if not self.db_path.exists(): return []
        backend = SQLiteSearchBackend(self.db_path)
        out: list[dict] = []
        seen: set[str] = set()
        for mid in message_ids:
            record = backend.get_message(int(mid))
            if record is None: continue
            seen.add(record.source_locator)
            out.append({"message_id": record.message_id, "author": record.author, "datetime": record.datetime_raw, "source_file": record.source_file, "source_ref": record.source_locator})
        for ref in source_refs:
            if ref in seen: continue
            out.append({"message_id": None, "author": None, "datetime": None, "source_file": ref.split("#",1)[0], "source_ref": ref})
        return out

    def health(self) -> dict:
        index = database_health(self.db_path)
        return {"bot": "up", "index": index, "ai_configured": self.ai_configured(), "provider_auth_failed": self.state.provider_auth_failed(), "model": self.model()}

    def stats(self) -> dict:
        index_stats = {}
        if self.db_path.exists(): index_stats = SQLiteSearchBackend(self.db_path).stats()
        return {"bot": self.state.usage_summary(), "ai": self.telemetry.summary(), "cache": self.cache.stats(), "index": index_stats, "model": self.model(), "access_mode": self.state.access_mode(), "rate_limit_per_minute": self.state.rate_limit_per_minute(), "last_reindex_at": self.state.last_reindex_at()}

    def clear_cache(self) -> int: return self.cache.clear()

    def reindex(self):
        if not self._reindex_lock.acquire(blocking=False): return None
        try:
            report = full_reindex(self.archive_dir, self.db_path)
            self.cache.clear()
            self.state.set_last_reindex_at(datetime.now(timezone.utc).isoformat())
            return report
        finally:
            self._reindex_lock.release()
