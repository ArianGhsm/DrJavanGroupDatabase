from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from datetime import datetime,timezone
import shutil
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
from drjavanbot.intelligence.archive_provider import archive_plan_from_request
from drjavanbot.intelligence.core import DentalIntelligenceCore
from drjavanbot.intelligence.model_policy import ModelPolicy
from drjavanbot.intelligence.models import SourceType
from drjavanbot.intelligence.planning import QuestionIntelligenceEngine
from drjavanbot.intelligence.provider import AvalAIIntelligenceProvider
from drjavanbot.intelligence.runtime import required_non_archive_sources, stage1_source_pending_answer
from drjavanbot.secrets import AVALAI_API_KEY_SECRET,LocalFileSecretStore
from drjavanbot.storage import database_health,full_reindex
from .config import ALLOWED_MODELS
from .contracts import IndexNotReadyError
from .state import BotStateStore
from .update_control import UpdateControl, VALID_UPDATE_MODES

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
    def answer(self,question): return self._answer(question,progress=None)
    def answer_with_progress(self,question,progress): return self._answer(question,progress=progress)
    def _answer(self,question,progress=None):
        if not self.db_path.exists(): raise IndexNotReadyError("index database does not exist")
        backend=SQLiteSearchBackend(self.db_path); config=self._ai_config(); precomputed_plan=None; intelligence_calls=0
        if config.intelligence_v2 or config.source_router_v2:
            intelligence_plan=self._intelligence_plan(question,config)
            intelligence_calls=0 if intelligence_plan.planner_fallback_used else 1
            if config.source_router_v2 and required_non_archive_sources(intelligence_plan.route):
                return stage1_source_pending_answer(intelligence_plan.route,ai_calls=intelligence_calls)
            if config.intelligence_v2:
                archive_request=next((item for item in intelligence_plan.retrieval_requests if item.source_type == SourceType.ARCHIVE),None)
                if archive_request is not None:
                    precomputed_plan=archive_plan_from_request(archive_request)
        service=ArchiveAnswerService(backend=backend,secret_store=self.secret_store,config=config,provider=DeepSeekV4AvalAIClient(config),cache=self.cache,planner_cache=self.planner_cache,telemetry=self.telemetry)
        try: result=service.answer(question,progress=progress,precomputed_plan=precomputed_plan)
        except AuthenticationError: self.state.set_provider_auth_failed(True); raise
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold(): raise IndexNotReadyError("archive index is temporarily busy") from exc
            raise
        self.state.set_provider_auth_failed(False)
        return result.with_runtime(ai_calls=result.ai_calls+intelligence_calls) if intelligence_calls else result
    def _intelligence_plan(self,question,config):
        provider=None
        if config.intelligence_v2:
            api_key=self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
            if api_key:
                provider=AvalAIIntelligenceProvider(api_key=api_key,base_config=config,telemetry=self.telemetry)
        engine=QuestionIntelligenceEngine(provider=provider,model_policy=ModelPolicy.from_env(default_model=self.model()))
        return DentalIntelligenceCore(question_engine=engine).plan(question)
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
    def health(self):
        update=self.updates.status()
        index_health=dict(database_health(self.db_path))
        # Admin UI consumes a single explicit boolean instead of inferring health
        # from message counts. Keep the storage-level `healthy` field unchanged.
        index_health["ok"]=bool(index_health.get("healthy",False))
        try:
            disk=shutil.disk_usage(self.data_dir)
            storage={"total_bytes":disk.total,"used_bytes":disk.used,"free_bytes":disk.free,"used_percent":round((disk.used/disk.total)*100,1) if disk.total else 0.0}
        except OSError: storage={}
        return {"bot":"up","index":index_health,"ai_configured":self.ai_configured(),"provider_auth_failed":self.state.provider_auth_failed(),"model":self.model(),"updater":{"state":update.state,"stage":update.stage,"target_sha":update.target_sha},"storage":storage}
    def stats(self):
        idx=SQLiteSearchBackend(self.db_path).stats() if self.db_path.exists() else {}; return {"bot":self.state.usage_summary(),"ai":self.telemetry.summary(),"cache":self.cache.stats(),"planner_cache":self.planner_cache.stats(),"index":idx,"model":self.model(),"access_mode":self.state.access_mode(),"rate_limit_per_minute":self.state.rate_limit_per_minute(),"last_reindex_at":self.state.last_reindex_at()}
    def clear_cache(self): return self.cache.clear()+self.planner_cache.clear()

    # Admin Control Center / updater contract.
    def update_mode(self): return self.state.update_mode()
    def set_update_mode(self,mode):
        if mode not in VALID_UPDATE_MODES: raise ValueError("invalid update mode")
        self.state.set_update_mode(mode)
    def request_software_update(self,*,source="manual"):
        return self.updates.request_verified_update(source=source)
    def request_rollback(self): return self.updates.request("rollback",source="manual")
    def update_status(self): return self.updates.status()
    def remote_update_info(self): return self.updates.remote_version()
    def cached_remote_update_info(self): return self.updates.cached_remote_version()
    def claim_update_notification(self,sha,kind="available"): return self.updates.claim_update_notification(sha,kind)
    def bind_update_progress_message(self,request_id,chat_id,message_id,*,target_sha=None): return self.updates.bind_progress_message(request_id,chat_id,message_id,target_sha=target_sha)
    def update_progress_binding(self): return self.updates.progress_binding()
    def clear_update_progress_binding(self,request_id=None): return self.updates.clear_progress_binding(request_id)
    def update_history(self,*,limit=10): return self.updates.history(limit=limit)
    def current_release_sha(self): return self.updates.current_release_sha()
    def previous_release_sha(self): return self.updates.previous_release_sha()

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
