from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from drjavanbot.telegram.app_v2 import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.update_control import UpdateControl, UpdateStatus


class FakeAPI:
    def __init__(self):
        self.sent=[]; self.edited=[]; self.callbacks=[]
    def send_message(self, chat_id, text, **kw): self.sent.append((chat_id,text,kw)); return {}
    def edit_message_text(self, chat_id, message_id, text, **kw): self.edited.append((chat_id,message_id,text,kw)); return {}
    def answer_callback(self, callback_id, **kw): self.callbacks.append(callback_id)


class FakeState:
    def access_mode(self): return "owner_only"


class FakeServices:
    def __init__(self): self.update_requests=0; self.rollback_requests=0
    def ai_configured(self): return True
    def model(self): return "deepseek-v4-flash"
    def update_status(self): return UpdateStatus(state="idle")
    def request_software_update(self): self.update_requests+=1; return "abc123"
    def request_rollback(self): self.rollback_requests+=1; return "def456"


def _app():
    return TelegramBotApp(
        api=FakeAPI(), owner_id=42, services=FakeServices(), state=FakeState(),
        config=TelegramConfig(),
    )


def _callback(user: int, data: str):
    return {"id":"cb1","from":{"id":user},"data":data,"message":{"message_id":9,"chat":{"id":user,"type":"private"}}}


def test_update_control_only_accepts_fixed_actions_and_atomic_request(tmp_path):
    control=UpdateControl(tmp_path/"data")
    request_id=control.request("update")
    payload=json.loads(control.request_path.read_text(encoding="utf-8"))
    assert payload["schema"]==1 and payload["action"]=="update" and payload["request_id"]==request_id
    assert control.status().state=="pending"
    with pytest.raises(RuntimeError): control.request("update")
    with pytest.raises(ValueError):
        UpdateControl(tmp_path/"other"/"data").request("shell:rm -rf /")


def test_update_control_reads_bounded_result(tmp_path):
    control=UpdateControl(tmp_path/"data")
    control.result_path.write_text(json.dumps({"state":"success","current_sha":"a"*40,"target_sha":"b"*40,"message":"ok"}),encoding="utf-8")
    status=control.status()
    assert status.state=="success" and status.target_sha=="b"*40


def test_owner_settings_expose_update_and_confirmation_requests_only_fixed_action():
    app=_app(); app._show_settings(42)
    keyboard=app.api.sent[-1][2]["reply_markup"]["inline_keyboard"]
    assert any(button["callback_data"]=="software_update" for row in keyboard for button in row)
    app._handle_callback(_callback(42,"software_update_confirm"))
    assert app.services.update_requests==1 and "درخواست آپدیت" in app.api.edited[-1][2]


def test_non_owner_cannot_trigger_update():
    app=_app(); app._handle_callback(_callback(7,"software_update_confirm"))
    assert app.services.update_requests==0 and "فقط برای مالک" in app.api.sent[-1][1]


def test_rollback_requires_owner_callback():
    app=_app(); app._handle_callback(_callback(42,"software_rollback_confirm"))
    assert app.services.rollback_requests==1


def _load_updater():
    path=Path(__file__).resolve().parents[1]/"deploy"/"self_update.py"
    spec=importlib.util.spec_from_file_location("drjavan_self_update_test",path)
    module=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
    return module


def test_privileged_updater_rejects_arbitrary_request_action(tmp_path, monkeypatch):
    module=_load_updater(); request=tmp_path/"request.json"
    request.write_text(json.dumps({"schema":1,"request_id":"x","action":"arbitrary-command"}),encoding="utf-8")
    monkeypatch.setattr(module,"REQUEST_FILE",request)
    with pytest.raises(module.UpdateFailure): module._read_request()


def test_updater_repo_and_service_targets_are_hard_coded():
    module=_load_updater()
    assert module.EXPECTED_REPO=="ArianGhsm/DrJavanGroupDatabase"
    assert module.SERVICE=="drjavanbot.service"
    assert all("DrJavanGroupDatabase" in remote for remote in module.ALLOWED_REMOTES)


def test_systemd_units_are_isolated_and_request_driven():
    root=Path(__file__).resolve().parents[1]
    bot=(root/"deploy/drjavanbot.service").read_text(encoding="utf-8")
    updater=(root/"deploy/drjavanbot-updater.service").read_text(encoding="utf-8")
    path_unit=(root/"deploy/drjavanbot-updater.path").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/drjavanbot/current" in bot
    assert "ExecStart=/opt/drjavanbot/current/.venv/bin/drjavanbot-bot" in bot
    assert "ExecStart=/usr/bin/python3 /opt/drjavanbot/app/deploy/self_update.py" in updater
    assert "PathExists=/var/lib/drjavanbot/update/request.json" in path_unit
    assert "Dent1402" not in bot+updater+path_unit and "VoiceMatn" not in bot+updater+path_unit
