from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from datetime import datetime,timezone
import sqlite3,threading
from drjavanbot.ai.cache_resilient import ResilientResponseCache
from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.key_manager import AvalAIKeyManager
from drjavanbot.ai.orchestrator import ArchiveAnswerService
from drjavanbot.ai.planner_cache import SearchPlanCache
from drjavanbot.ai.provider import AuthenticationError
from drjavanbot.ai.provider_v4 import DeepSeekV4AvalAIClient
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.secrets import AVALAI_API_KEY_SECRET,LocalFileSecretStore
from drjavanbot.storage import database_health,full_reindex
from .config import ALLOWED_MODELS
from .contracts import IndexNotReadyError
from .state import BotStateStore
from .update_control import UpdateControl

class RuntimeServices:
    def __init__(self,*,archive_dir:Path,data_dir:Path,cache_dir:Path,secret_dir:Path,base_ai_config:AIConfig,state:BotStateStore):
        self.archive_dir=archive_dir; self.data_dir=data_dir; self.db_path=data_dir/"archive.sqlite3"; self.state=state; self.base_ai_config=base_ai_config; self.secret_store=LocalFileSecretStore(secret_dir); self.cache=ResilientResponseCache(cache_dir/"ai_responses.sqlite3",ttl_seconds=base_ai_config.cache_ttl_seconds); self.planner_cache=SearchPlanCache(cache_dir/"search_plans.sqlite3",ttl_seconds=base_ai_config.cache_ttl_seconds); self.telemetry=TelemetryStore(data_dir/"ai_usage.sqlite3"); self.updates=UpdateControl(data_dir); self._reindex_lock=threading.Lock(); self._reindex_lock_path=data_dir/"reindex.lock"
    def model(self):
        m=self.state.selected_model(self.base_ai_config.model); return m if m in ALLOWED_MODELS else self.base_ai_config.model
    def set_model(self,m):
        if m not in ALLOWED_MODELS: raise ValueError("model is not allowed")
        self.state.set_model(m)
    def ai_configured(self): return self.secret_store.is_configured(AVALAI_API_KEY_SECRET)
    def _ai_config(self): return replace(self.base_ai_config,model=self.model())
    def key_manager(self): return AvalAIKeyManager(self.secret_store,DeepSeekV4AvalAIClient(self._ai_config()))
    def set_api_key(self,candidate):
        ok=self.key_manager().validate_and_store(candidate)
        if ok: self.state.set_provider_auth_failed(False)
        return ok
    def remove_api_key(self):
        removed=self.key_manager().remove(); self.state.set_provider_auth_failed(False); return removed
    def test_ai(self):
        key=self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
        if not key: return False
        ok=DeepSeekV4AvalAIClient(self._ai_config()).validate_api_key(key); self.state.set_provider_auth_failed(not ok); return ok
    def answer(self,question):
        if not self.db_path.exists(): raise IndexNotReadyError("index database does not exist")
        backend=SQLiteSearchBackend(self.db_path); config=self._ai_config(); service=ArchiveAnswerService(backend=backend,secret_store=self.secret_store,config=config,provider=DeepSeekV4AvalAIClient(config),cache=self.cache,planner_cache=self.planner_cache,telemetry=self.telemetry)
        try: result=service.answer(question)
        except AuthenticationError: self.state.set_provider_auth_failed(True); raise
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold(): raise IndexNotReadyError("archive index is temporarily busy") from exc
            raise
        self.state.set_provider_auth_failed(False); return result
    def source_details(self,message_ids,source_refs):
        if not self.db_path.exists(): return []
        backend=SQLiteSearchBackend(self.db_path); out=[]; seen=set()
        for mid in message_ids:
            r=backend.get_message(int(mid))
            if r is None: continue
            seen.add(r.source_locator); out.append({"message_id":r.message_id,"author":r.author,"datetime":r.datetime_raw,"source_file":r.source_file,"source_ref":r.source_locator})
        for ref in source_refs:
            if ref not in seen: out.append({"message_id":None,"author":None,"datetime":None,"source_file":ref.split("#",1)[0],"source_ref":ref})
        return out
    def health(self): return {"bot":"up","index":database_health(self.db_path),"ai_configured":self.ai_configured(),"provider_auth_failed":self.state.provider_auth_failed(),"model":self.model()}
    def stats(self):
        idx=SQLiteSearchBackend(self.db_path).stats() if self.db_path.exists() else {}; return {"bot":self.state.usage_summary(),"ai":self.telemetry.summary(),"cache":self.cache.stats(),"planner_cache":self.planner_cache.stats(),"index":idx,"model":self.model(),"access_mode":self.state.access_mode(),"rate_limit_per_minute":self.state.rate_limit_per_minute(),"last_reindex_at":self.state.last_reindex_at()}
    def clear_cache(self): return self.cache.clear()+self.planner_cache.clear()
    def request_software_update(self): return self.updates.request("update")
    def request_rollback(self): return self.updates.request("rollback")
    def update_status(self): return self.updates.status()
    def remote_update_info(self): return self.updates.remote_version()
    def claim_update_notification(self,sha): return self.updates.claim_update_notification(sha)
    def reindex(self):
        if not self._reindex_lock.acquire(blocking=False): return None
        handle=None; locked=False
        try:
            self._reindex_lock_path.parent.mkdir(parents=True,exist_ok=True); handle=self._reindex_lock_path.open("a+")
            try:
                import fcntl
                try: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB); locked=True
                except BlockingIOError: return None
            except ImportError: pass
            report=full_reindex(self.archive_dir,self.db_path); self.cache.clear(); self.planner_cache.clear(); self.state.set_last_reindex_at(datetime.now(timezone.utc).isoformat()); return report
        finally:
            if handle is not None:
                if locked:
                    try:
                        import fcntl; fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
                    except (ImportError,OSError): pass
                handle.close()
            self._reindex_lock.release()
