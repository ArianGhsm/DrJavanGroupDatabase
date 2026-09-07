from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading

from drjavanbot.telegram.app import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.state import BotStateStore
from test_stage4_telegram import FakeAPI, FakeServices, msg, cb


def test_update_claim_release_complete_is_restart_safe():
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/"state.db"; s=BotStateStore(p)
        assert s.claim_update(10, now=100.0, lease_seconds=10)
        assert not s.claim_update(10, now=105.0, lease_seconds=10)
        s.release_update(10)
        assert s.claim_update(10, now=106.0, lease_seconds=10)
        s.complete_update(10, now=107.0)
        assert not BotStateStore(p).claim_update(10, now=500.0, lease_seconds=10)


def test_rate_limit_is_atomic_under_concurrency():
    with tempfile.TemporaryDirectory() as td:
        s=BotStateStore(Path(td)/"state.db", default_rate_limit=3)
        s.set_access_mode("public")
        barrier=threading.Barrier(12)
        def take(_):
            barrier.wait(); return s.consume_rate_slot(7, owner_id=42, now=1000.0)
        with ThreadPoolExecutor(max_workers=12) as ex:
            results=list(ex.map(take, range(12)))
        assert sum(results)==3


def test_malformed_owner_callbacks_are_ignored_not_crashing():
    with tempfile.TemporaryDirectory() as td:
        s=BotStateStore(Path(td)/"state.db"); api=FakeAPI(); svc=FakeServices()
        app=TelegramBotApp(api=api,owner_id=42,services=svc,state=s,config=TelegramConfig())
        app.handle_update(cb(1,42,"access:evil"))
        app.handle_update(cb(2,42,"rate:not-a-number"))
        assert s.access_mode()=="owner_only"
        assert s.rate_limit_per_minute()==6


class FailHtmlOnceAPI(FakeAPI):
    def __init__(self): super().__init__(); self.failed=False
    def send_message(self,chat_id,text,**kw):
        if not self.failed and kw.get("parse_mode","HTML")=="HTML" and "پاسخ" in text:
            self.failed=True
            from drjavanbot.telegram.api import TelegramAPIError
            raise TelegramAPIError("format")
        return super().send_message(chat_id,text,**kw)


def test_answer_html_failure_falls_back_to_plain_text():
    with tempfile.TemporaryDirectory() as td:
        s=BotStateStore(Path(td)/"state.db"); api=FailHtmlOnceAPI(); svc=FakeServices()
        app=TelegramBotApp(api=api,owner_id=42,services=svc,state=s,config=TelegramConfig())
        app.handle_update(msg(1,42,"private","normal"))
        assert api.failed
        assert any(x[2].get("parse_mode") is None for x in api.sent)
        assert any("پاسخ & مستند" in x[1] for x in api.sent)

def test_polling_does_not_advance_past_failed_update(monkeypatch):
    import importlib, sys, types
    # Runtime imports deployment wiring that is irrelevant to PollingRunner; stub it.
    svcmod=types.ModuleType('drjavanbot.telegram.services'); svcmod.RuntimeServices=object
    sys.modules['drjavanbot.telegram.services']=svcmod
    cfgmod=types.ModuleType('drjavanbot.config')
    class Settings: pass
    cfgmod.Settings=Settings; sys.modules['drjavanbot.config']=cfgmod
    aimod=types.ModuleType('drjavanbot.ai.config')
    class AIConfig: pass
    aimod.AIConfig=AIConfig; sys.modules['drjavanbot.ai.config']=aimod
    sys.modules.pop('drjavanbot.telegram.runtime',None)
    runtime=importlib.import_module('drjavanbot.telegram.runtime')

    class A:
        def __init__(self): self.calls=0; self.offsets=[]
        def get_me(self): return {'id':1}
        def get_updates(self,*,offset,timeout):
            self.offsets.append(offset); self.calls+=1
            if self.calls==1: return [{'update_id':10},{'update_id':11}]
            runner.stop_event.set(); return []
    class App:
        def handle_update(self,u):
            if u['update_id']==10: raise RuntimeError('boom')
    api=A(); runner=runtime.PollingRunner(App(),api,TelegramConfig(worker_count=2,poll_timeout_seconds=5))
    runner.run()
    assert api.offsets[:2]==[None,10]

