from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from datetime import datetime,timezone
import shutil
import logging
import sqlite3,threading
from drjavanbot.ai.cache import ResponseCache
from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.key_manager import AvalAIKeyManager
from drjavanbot.ai.orchestrator import ArchiveAnswerService
from drjavanbot.ai.planner_cache import SearchPlanCache
from drjavanbot.ai.provider import AuthenticationError
from drjavanbot.ai.provider import DeepSeekV4AvalAIClient
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.intelligence.archive_provider import archive_plan_from_request
from drjavanbot.intelligence.cache import SourceAwareResponseCache
from drjavanbot.intelligence.conversation import ConversationQuestionContext
from drjavanbot.intelligence.core import DentalIntelligenceCore
from drjavanbot.intelligence.model_policy import ModelPolicy
from drjavanbot.intelligence.models import FreshnessClass, SourceRequirement, SourceRoute, SourceSelection, SourceType
from drjavanbot.intelligence.query_generation import generate_retrieval_requests
from drjavanbot.intelligence.planning import QuestionIntelligenceEngine
from drjavanbot.intelligence.provider import AvalAIIntelligenceProvider
from drjavanbot.intelligence.service import MultiSourceAnswerService
from drjavanbot.secrets import AVALAI_API_KEY_SECRET,LocalFileSecretStore
from drjavanbot.storage import database_health,full_reindex
from .config import ALLOWED_MODELS
from .contracts import IndexNotReadyError
from .state import BotStateStore
from .update_control import UpdateControl, VALID_UPDATE_MODES

_LOG=logging.getLogger(__name__)
_EXTERNAL_SOURCES=frozenset({str(SourceType.SCIENTIFIC),str(SourceType.CURRENT_WEB),str(SourceType.OFFICIAL)})
_TIME_SENSITIVE_SOURCES=frozenset({str(SourceType.CURRENT_WEB),str(SourceType.OFFICIAL)})


def _follow_up_archive_plan(plan):
    """A follow-up ("و قیمتش؟") borrows its topic from the conversation; only
    question intelligence knows that topic, so its archive plan replaces the
    raw-question planner. Self-contained questions keep the archive planner."""
    understanding=getattr(plan,"understanding",None)
    entities=tuple(getattr(understanding,"entities",()) or ())
    if not any(getattr(item,"inferred",False) and getattr(item,"entity_type","") not in {"profession","career_stage"} for item in entities):
        return None
    route=SourceRoute((SourceSelection(str(SourceType.ARCHIVE),100,str(SourceRequirement.REQUIRED),str(FreshnessClass.UNSPECIFIED),"conversation_follow_up","discussion_graph_topic_lookup"),),(str(SourceType.ARCHIVE),),("conversation_follow_up",))
    request=next(iter(generate_retrieval_requests(understanding,route)),None)
    return archive_plan_from_request(request) if request is not None and request.queries else None


class RuntimeServices:
    def __init__(self,*,archive_dir:Path,data_dir:Path,cache_dir:Path,secret_dir:Path,base_ai_config:AIConfig,state:BotStateStore):
        self.archive_dir=archive_dir; self.data_dir=data_dir; self.db_path=data_dir/"archive.sqlite3"; self.state=state; self.base_ai_config=base_ai_config; self.secret_store=LocalFileSecretStore(secret_dir); self.cache=ResponseCache(cache_dir/"ai_responses.sqlite3",ttl_seconds=base_ai_config.cache_ttl_seconds); self.intelligence_cache=SourceAwareResponseCache(cache_dir/"intelligence_answers.sqlite3"); self.planner_cache=SearchPlanCache(cache_dir/"search_plans.sqlite3",ttl_seconds=base_ai_config.cache_ttl_seconds); self.telemetry=TelemetryStore(data_dir/"ai_usage.sqlite3"); self.updates=UpdateControl(data_dir); self._reindex_lock=threading.Lock(); self._reindex_lock_path=data_dir/"reindex.lock"
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
    def answer(self,question,*,user_id=None): return self._answer(question,progress=None,user_id=user_id)
    def answer_with_progress(self,question,progress,*,user_id=None): return self._answer(question,progress=progress,user_id=user_id)
    def _answer(self,question,progress=None,user_id=None):
        """One answer pipeline for every question.

        1. Question intelligence (deterministic, AI only when ambiguous) records
           conversation context and decides which sources the question needs.
        2. Time-sensitive questions (prices, salaries, regulation) go to the
           current/official sources first, with the archive as a supplement.
        3. Everything else is answered from the group archive first; external
           scientific sources are consulted only when the archive has no answer.
        """
        if not self.db_path.exists(): raise IndexNotReadyError("index database does not exist")
        backend=SQLiteSearchBackend(self.db_path); config=self._ai_config()
        plan=self._intelligence_plan(question,config,user_id=user_id)
        intelligence_calls=1 if (plan.model_decision is not None and not plan.planner_fallback_used) else 0
        if user_id is not None:
            try: self.state.set_conversation_context(int(user_id), ConversationQuestionContext.from_understanding(plan.understanding))
            except Exception: pass
        required={str(value) for value in plan.route.required_sources}
        external=required & _EXTERNAL_SOURCES
        if required & _TIME_SENSITIVE_SOURCES:
            result=self._multi_source_answer(backend,config,plan,progress,intelligence_calls)
            if not result.insufficient_evidence: return result
            return self._archive_answer(backend,config,question,plan,progress,result.ai_calls)
        result=self._archive_answer(backend,config,question,plan,progress,intelligence_calls)
        if result.insufficient_evidence and external:
            # The fallback is best effort: a failing external source must not
            # replace the archive's honest "not enough evidence" answer with an
            # error. Authentication failures still surface to the owner.
            try: fallback=self._multi_source_answer(backend,config,plan,progress,result.ai_calls)
            except AuthenticationError: raise
            except Exception as exc:
                _LOG.warning("external_fallback_failed error_class=%s",type(exc).__name__); return result
            if not fallback.insufficient_evidence: return fallback
        return result
    def _archive_answer(self,backend,config,question,plan,progress,intelligence_calls):
        service=ArchiveAnswerService(backend=backend,secret_store=self.secret_store,config=config,provider=DeepSeekV4AvalAIClient(config),cache=self.cache,planner_cache=self.planner_cache,telemetry=self.telemetry)
        try: result=service.answer(question,progress=progress,precomputed_plan=_follow_up_archive_plan(plan))
        except AuthenticationError: self.state.set_provider_auth_failed(True); raise
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold(): raise IndexNotReadyError("archive index is temporarily busy") from exc
            raise
        self.state.set_provider_auth_failed(False)
        return result.with_runtime(ai_calls=result.ai_calls+intelligence_calls) if intelligence_calls else result
    def _multi_source_answer(self,backend,config,plan,progress,prior_calls):
        multi=MultiSourceAnswerService(backend=backend,secret_store=self.secret_store,config=config,cache=self.intelligence_cache,telemetry=self.telemetry,model_policy=ModelPolicy.from_env(default_model=self.model()))
        try: result=multi.answer(plan,progress=progress,understanding_ai_calls=prior_calls)
        except AuthenticationError: self.state.set_provider_auth_failed(True); raise
        self.state.set_provider_auth_failed(False); return result
    def _intelligence_plan(self,question,config,*,user_id=None):
        provider=None
        api_key=self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
        if api_key:
            provider=AvalAIIntelligenceProvider(api_key=api_key,base_config=config,telemetry=self.telemetry)
        qcontext=None
        if user_id is not None:
            try: qcontext=self.state.get_conversation_context(int(user_id)).to_question_context()
            except Exception: qcontext=None
        engine=QuestionIntelligenceEngine(provider=provider,model_policy=ModelPolicy.from_env(default_model=self.model()),context=qcontext)
        return DentalIntelligenceCore(question_engine=engine).plan(question)
    def source_details(self,message_ids,source_refs,external_sources=()):
        if not self.db_path.exists(): return []
        backend=SQLiteSearchBackend(self.db_path); out=[]; seen=set(); external_by_ref={str(item.get("source_ref") or ""):item for item in external_sources if isinstance(item,dict)}
        for mid in message_ids:
            r=backend.get_message(int(mid))
            if r is None: continue
            seen.add(r.source_locator); out.append({"message_id":r.message_id,"author":r.author,"datetime":r.datetime_raw,"source_file":r.source_file,"source_ref":r.source_locator})
        for ref in source_refs:
            if ref in seen: continue
            ext=external_by_ref.get(str(ref))
            if ext is not None:
                out.append({"message_id":None,"author":ext.get("author_or_org"),"datetime":ext.get("timestamp") or ext.get("publication_year"),"source_file":ext.get("source_name") or ext.get("title"),"source_ref":ref,"source_type":ext.get("source_type"),"title":ext.get("title"),"url":ext.get("url"),"publication_type":ext.get("publication_type")}); seen.add(ref); continue
            out.append({"message_id":None,"author":None,"datetime":None,"source_file":ref.split("#",1)[0],"source_ref":ref})
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
        idx=SQLiteSearchBackend(self.db_path).stats() if self.db_path.exists() else {}; return {"bot":self.state.usage_summary(),"ai":self.telemetry.summary(),"cache":self.cache.stats(),"planner_cache":self.planner_cache.stats(),"intelligence_cache":self.intelligence_cache.stats(),"index":idx,"model":self.model(),"access_mode":self.state.access_mode(),"rate_limit_per_minute":self.state.rate_limit_per_minute(),"last_reindex_at":self.state.last_reindex_at()}
    def clear_cache(self): return self.cache.clear()+self.planner_cache.clear()+self.intelligence_cache.clear()

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
