from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import logging
import tempfile
import time

from drjavanbot.ai.provider import AuthenticationError, ProviderTimeoutError, RateLimitError
from drjavanbot.telegram.app import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.rendering import answer_chunks
from drjavanbot.telegram.state import BotStateStore

@dataclass
class Answer:
    direct_answer: str = "پاسخ"
    key_findings: tuple[str,...] = ()
    disagreements: tuple[str,...] = ()
    practical_conclusion: str|None = None
    confidence: str = "high"
    confidence_reason: str = "شواهد متعدد"
    cited_message_ids: tuple[int,...] = (10,11,12,13,14,15,16)
    source_refs: tuple[str,...] = ()
    evidence_used_count: int = 2
    independent_authors_count: int = 2
    insufficient_evidence: bool = False
    safety_note_if_needed: str|None = None
    cache_hit: bool = False
    ai_calls: int = 1

class FakeCache:
    def __init__(self): self.cleared=0
    def stats(self): return type("S",(),dict(entries=2,hits=3,misses=4,expired_entries=0))()
    def clear(self): self.cleared+=1; return 2

class FakeServices:
    def __init__(self):
        self.configured=True; self.key=None; self.removed=False; self._model="deepseek-v4-flash"; self.cache=FakeCache(); self.raise_exc=None; self.reindex_calls=0
    def model(self): return self._model
    def set_model(self,m): self._model=m
    def ai_configured(self): return self.configured
    def set_api_key(self,k):
        if k=="valid-key-123": self.key=k; return True
        return False
    def remove_api_key(self): self.removed=True; self.configured=False; return True
    def test_ai(self): return self.configured
    def answer(self,q):
        if self.raise_exc: raise self.raise_exc
        return Answer(direct_answer=("الف"*9000 if q=="long" else "پاسخ & مستند"))
    def source_details(self,ids,refs): return [{"message_id":i,"author":f"A{i}","datetime":"2026-01-01","source_file":f"messages{i}.html","source_ref":f"messages{i}.html#go_to_message{i}"} for i in ids]
    def health(self): return {"index":{"healthy":True},"ai_configured":self.configured,"provider_auth_failed":False,"model":self._model}
    def stats(self):
        z=type("Z",(),{})(); z.questions=1;z.successes=1;z.failures=0;z.cache_hits=0;z.ai_calls=1;z.average_latency_ms=5
        a=type("A",(),{})();a.calls=1;a.successes=1;a.failures=0;a.input_tokens=100;a.output_tokens=20;a.cached_input_tokens=0;a.cost_irt=12.5;a.average_latency_ms=50
        return {"bot":z,"ai":a,"cache":self.cache.stats(),"index":{"messages":100},"access_mode":"owner_only","rate_limit_per_minute":6,"last_reindex_at":None}
    def clear_cache(self): return self.cache.clear()
    def reindex(self): self.reindex_calls+=1; return type("R",(),dict(archive_files=247,messages=1000))()

class FakeAPI:
    def __init__(self): self.sent=[];self.edited=[];self.deleted=[];self.callbacks=[];self.actions=[]
    def send_message(self,chat_id,text,**kw): self.sent.append((chat_id,text,kw)); return {"message_id":len(self.sent)}
    def edit_message_text(self,chat_id,message_id,text,**kw): self.edited.append((chat_id,message_id,text,kw)); return {}
    def delete_message(self,chat_id,message_id): self.deleted.append((chat_id,message_id)); return True
    def answer_callback(self,cqid,text=None,**kw): self.callbacks.append(cqid)
    def send_chat_action(self,chat_id,action="typing"): self.actions.append((chat_id,action))

def msg(update_id,user,chat_type,text,mid=1): return {"update_id":update_id,"message":{"message_id":mid,"from":{"id":user},"chat":{"id":user,"type":chat_type},"text":text}}
def cb(update_id,user,data,mid=99): return {"update_id":update_id,"callback_query":{"id":str(update_id),"from":{"id":user},"data":data,"message":{"message_id":mid,"chat":{"id":user,"type":"private"}}}}

def make():
    td=tempfile.TemporaryDirectory(); state=BotStateStore(Path(td.name)/"state.db",default_rate_limit=2); api=FakeAPI();svc=FakeServices(); app=TelegramBotApp(api=api,owner_id=42,services=svc,state=state,config=TelegramConfig(default_rate_limit_per_minute=2,max_question_chars=2000,key_entry_timeout_seconds=300,source_session_ttl_seconds=3600)); return td,state,api,svc,app

def test_owner_auth_private_and_non_owner_denial():
    td,s,a,v,app=make(); app.handle_update(msg(1,7,"private","/settings")); assert "فقط برای مالک" in a.sent[-1][1]; app.handle_update(msg(2,42,"group","/settings")); assert "گفت‌وگوی خصوصی" in a.sent[-1][1]; app.handle_update(msg(3,42,"private","/settings")); assert "تنظیمات مالک" in a.sent[-1][1]; td.cleanup()

def test_key_flow_deletes_message_and_does_not_echo_key(caplog):
    td,s,a,v,app=make(); app.handle_update(cb(1,42,"setkey")); caplog.set_level(logging.DEBUG); app.handle_update(msg(2,42,"private","valid-key-123",mid=55)); assert (42,55) in a.deleted; assert v.key=="valid-key-123"; rendered=" ".join(x[1] for x in a.sent); assert "valid-key-123" not in rendered; assert "valid-key-123" not in caplog.text; td.cleanup()

def test_invalid_key_preserves_previous_key():
    td,s,a,v,app=make(); v.key="old"; app.handle_update(cb(1,42,"setkey")); app.handle_update(msg(2,42,"private","bad-key",mid=5)); assert v.key=="old"; assert "نامعتبر" in a.sent[-1][1]; td.cleanup()

def test_remove_requires_confirmation():
    td,s,a,v,app=make(); app.handle_update(cb(1,42,"remove_key")); assert not v.removed; app.handle_update(cb(2,42,"remove_key_yes")); assert v.removed; td.cleanup()

def test_startup_without_key_and_question_guard():
    td,s,a,v,app=make(); v.configured=False; app.handle_update(msg(1,42,"private","سؤال")); assert "هنوز توسط مدیر تنظیم نشده" in a.sent[-1][1]; td.cleanup()

def test_access_default_owner_only_allowlist_public():
    td,s,a,v,app=make(); app.handle_update(msg(1,7,"private","q")); assert "دسترسی" in a.sent[-1][1]; s.add_allowed_user(7); s.set_access_mode("allowlist"); app.handle_update(msg(2,7,"private","q")); assert "پاسخ" in a.sent[-1][1]; s.set_access_mode("public"); app.handle_update(msg(3,8,"private","q")); assert "پاسخ" in a.sent[-1][1]; td.cleanup()

def test_rate_limit_and_owner_bypass():
    td,s,a,v,app=make(); s.set_access_mode("public"); app.handle_update(msg(1,7,"private","a")); app.handle_update(msg(2,7,"private","b")); app.handle_update(msg(3,7,"private","c")); assert "بیش از حد" in a.sent[-1][1]; [app.handle_update(msg(10+i,42,"private",f"o{i}")) for i in range(4)]; assert "بیش از حد" not in a.sent[-1][1]; td.cleanup()

def test_duplicate_update_only_once():
    td,s,a,v,app=make(); u=msg(1,42,"private","q"); app.handle_update(u); count=len(a.sent); app.handle_update(u); assert len(a.sent)==count; td.cleanup()

def test_long_answer_is_split_and_escaped():
    td,s,a,v,app=make(); app.handle_update(msg(1,42,"private","long")); payload=[x[1] for x in a.sent if "الف" in x[1]]; assert len(payload)>=3; assert all(len(x)<=3800 for x in payload); app.handle_update(msg(2,42,"private","normal")); assert "&amp;" in a.sent[-1][1]; td.cleanup()

def test_sources_pagination_is_user_bound():
    td,s,a,v,app=make(); app.handle_update(msg(1,42,"private","q")); markup=a.sent[-1][2]["reply_markup"]; data=markup["inline_keyboard"][0][0]["callback_data"]; app.handle_update(cb(2,42,data)); assert "صفحه 1/2" in a.edited[-1][2]; before=len(a.edited); app.handle_update(cb(3,7,data)); assert len(a.edited)==before; td.cleanup()

def test_model_allowlist_and_settings():
    td,s,a,v,app=make(); app.handle_update(cb(1,42,"model:deepseek-v4-pro")); assert v.model()=="deepseek-v4-pro"; app.handle_update(cb(2,42,"model:evil")); assert v.model()=="deepseek-v4-pro"; td.cleanup()

def test_provider_errors_are_user_friendly_and_auth_not_delete_key():
    for exc,needle in [(AuthenticationError("x"),"نیازمند بررسی"),(RateLimitError("x"),"محدودیت"),(ProviderTimeoutError("x"),"زمان مقرر")]:
        td,s,a,v,app=make(); v.raise_exc=exc; app.handle_update(msg(1,42,"private","q")); assert needle in a.sent[-1][1]; assert not v.removed; td.cleanup()

def test_state_flow_and_source_session_ownership():
    td,s,a,v,app=make(); s.begin_flow(42,"await_avalai_key",1); assert s.active_flow(42); sid=s.create_source_session(42,[{"x":1}],1); assert s.get_source_session(sid,42)==[{"x":1}]; assert s.get_source_session(sid,7) is None; td.cleanup()

def test_answer_chunks_limit():
    a=Answer(direct_answer="x"*10000); chunks=answer_chunks(a); assert len(chunks)>=3 and all(len(x)<=3800 for x in chunks)

def test_health_stats_index_callbacks_are_owner_only_and_render():
    td,s,a,v,app=make(); app.handle_update(cb(20,42,"health")); assert "Health" in a.edited[-1][2]; app.handle_update(cb(21,42,"stats")); assert "آمار" in a.edited[-1][2]; app.handle_update(cb(22,42,"indexstats")); assert "Index" in a.edited[-1][2]; before=len(a.edited); app.handle_update(cb(23,7,"stats")); assert len(a.edited)==before and "فقط برای مالک" in a.sent[-1][1]; td.cleanup()

def test_reindex_busy_status_is_reported():
    td,s,a,v,app=make(); v.reindex=lambda: None; app.handle_update(cb(30,42,"reindex")); deadline=time.time()+1
    while time.time()<deadline and not any("reindex دیگر" in x[1] for x in a.sent): time.sleep(0.01)
    assert any("reindex دیگر" in x[1] for x in a.sent); td.cleanup()
