from __future__ import annotations

from types import SimpleNamespace

from drjavanbot.telegram.app_v2 import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.runtime import PollingRunner
from drjavanbot.telegram.update_control import UpdateStatus


class FakeAPI:
    def __init__(self):
        self.sent=[]
        self.command_calls=[]
    def send_message(self,chat_id,text,**kwargs):
        self.sent.append((chat_id,text,kwargs)); return {}
    def set_my_commands(self,commands,**kwargs):
        self.command_calls.append((list(commands),kwargs)); return True


class FakeState:
    def access_mode(self): return "owner_only"


class FakeServices:
    def ai_configured(self): return True
    def model(self): return "deepseek-v4-flash"
    def update_status(self): return UpdateStatus(state="idle")


def test_owner_start_has_direct_control_center_destinations():
    api=FakeAPI()
    app=TelegramBotApp(api=api,owner_id=42,services=FakeServices(),state=FakeState(),config=TelegramConfig())
    app._handle_command(42,42,True,"/start")
    assert api.sent
    keyboard=api.sent[-1][2]["reply_markup"]["inline_keyboard"]
    callbacks={button["callback_data"] for row in keyboard for button in row}
    assert callbacks == {"adm:update","adm:health","adm:ai","adm:archive","adm:access","adm:tools"}
    text=str(api.sent[-1][1])
    assert "مرکز مدیریت" in text
    assert "به‌روزرسانی" in text
    assert "ایندکس" in text


def test_panel_alias_opens_owner_control_center():
    api=FakeAPI()
    app=TelegramBotApp(api=api,owner_id=42,services=FakeServices(),state=FakeState(),config=TelegramConfig())
    app._handle_command(42,42,True,"/panel")
    text=str(api.sent[-1][1])
    assert "مرکز مدیریت" in text
    assert "وضعیت کلی" in text


def test_runtime_registers_owner_scoped_command_menu_without_affecting_startup():
    api=FakeAPI()
    app=SimpleNamespace(owner_id=42)
    runner=PollingRunner(app,api,TelegramConfig())
    try:
        runner._configure_command_menus()
    finally:
        runner.executor.shutdown(wait=True,cancel_futures=True)
    assert len(api.command_calls)==2
    public,public_kwargs=api.command_calls[0]
    owner,owner_kwargs=api.command_calls[1]
    assert public_kwargs=={}
    assert owner_kwargs["scope"]=={"type":"chat","chat_id":42}
    owner_commands={item["command"] for item in owner}
    assert {"panel","update","errors","health"}.issubset(owner_commands)
    owner_descriptions=" ".join(item["description"] for item in owner)
    assert "به‌روزرسانی نرم‌افزار" in owner_descriptions
    assert "سلامت سیستم" in owner_descriptions
