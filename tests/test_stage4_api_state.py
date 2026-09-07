from pathlib import Path
import json
import tempfile

from drjavanbot.telegram.api import TelegramAPI, TelegramResponse, TelegramUnauthorizedError
from drjavanbot.telegram.state import BotStateStore

class Transport:
    def __init__(self,status=200,payload=None): self.status=status; self.payload=payload or {"ok":True,"result":{"id":1}}; self.urls=[]; self.bodies=[]
    def request(self,url,body,timeout): self.urls.append(url);self.bodies.append(body);return TelegramResponse(self.status,json.dumps(self.payload).encode())

def test_api_repr_redacts_token_and_serializes_markup():
    t=Transport(payload={"ok":True,"result":{"message_id":1}}); api=TelegramAPI("123:secret-token",transport=t)
    api.send_message(1,"hi",reply_markup={"inline_keyboard":[]})
    assert "secret-token" not in repr(api)
    assert b"reply_markup" in t.bodies[-1]

def test_api_unauthorized_error_does_not_expose_token():
    t=Transport(status=401,payload={"ok":False,"error_code":401}); api=TelegramAPI("123:secret-token",transport=t)
    try: api.get_me()
    except TelegramUnauthorizedError as exc:
        assert "secret-token" not in str(exc)
    else: raise AssertionError("expected unauthorized")

def test_state_default_is_owner_only_and_rate_is_bounded():
    with tempfile.TemporaryDirectory() as td:
        s=BotStateStore(Path(td)/"state.db",default_rate_limit=6)
        assert s.access_mode()=="owner_only"
        assert s.is_allowed(42,42)
        assert not s.is_allowed(7,42)
        s.set_access_mode("public")
        assert s.is_allowed(7,42)
        s.set_rate_limit_per_minute(3)
        assert s.rate_limit_per_minute()==3

def test_processed_update_dedup_is_persistent():
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/"state.db"; s=BotStateStore(p)
        assert s.mark_update_once(123) is True
        assert s.mark_update_once(123) is False
        assert BotStateStore(p).mark_update_once(123) is False
