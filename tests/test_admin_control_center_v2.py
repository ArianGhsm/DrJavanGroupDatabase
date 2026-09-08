from __future__ import annotations

from types import SimpleNamespace

from drjavanbot.telegram.app_v2 import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.update_control import RemoteUpdateInfo, UpdateStatus


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.callbacks = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, str(text), kwargs))
        return {"message_id": 70 + len(self.sent)}

    def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edited.append((chat_id, message_id, str(text), kwargs))
        return {"message_id": message_id}

    def answer_callback(self, callback_id, **kwargs):
        self.callbacks.append(callback_id)


class FakeState:
    path = ":memory:"

    def __init__(self):
        self.mode = "owner_only"
        self.rate = 6
        self.update = "notify"
        self.flow = None

    def access_mode(self): return self.mode
    def set_access_mode(self, value): self.mode = value
    def rate_limit_per_minute(self): return self.rate
    def set_rate_limit_per_minute(self, value): self.rate = value
    def update_mode(self): return self.update
    def set_update_mode(self, value): self.update = value
    def begin_flow(self, user_id, flow, ttl_seconds): self.flow = flow


class FakeCacheStats:
    entries = 3
    hits = 9
    misses = 2
    expired_entries = 1


class FakeCache:
    def stats(self): return FakeCacheStats()


class FakeServices:
    cache = FakeCache()

    def __init__(self, *, ci_status="success", available=True):
        self.ci_status = ci_status
        self.available = available
        self.requests = []
        self.bindings = []
        self.mode = "notify"
        self.status_value = UpdateStatus(state="idle", current_sha="a" * 40)
        self.binding = None
        self.notifications = set()

    def ai_configured(self): return True
    def model(self): return "deepseek-v4-flash"
    def set_model(self, model): self.selected = model
    def test_ai(self): return True
    def remove_api_key(self): return True
    def clear_cache(self): return 3
    def health(self):
        return {
            "bot": "up",
            "index": {"healthy": True, "messages": 249907},
            "ai_configured": True,
            "provider_auth_failed": False,
            "updater": {"state": self.status_value.state},
            "storage": {"used_percent": 44.2},
        }
    def stats(self):
        return {
            "index": {"messages": 249907},
            "last_reindex_at": "2026-09-08T10:00:00+00:00",
            "bot": SimpleNamespace(questions=10, successes=9, failures=1, average_latency_ms=1200),
        }
    def update_mode(self): return self.mode
    def set_update_mode(self, mode): self.mode = mode
    def update_status(self): return self.status_value
    def current_release_sha(self): return "a" * 40
    def previous_release_sha(self): return "9" * 40
    def remote_update_info(self):
        return RemoteUpdateInfo(
            current_sha="a" * 40,
            latest_sha="b" * 40,
            update_available=self.available,
            ci_status=self.ci_status,
        )
    def cached_remote_update_info(self): return self.remote_update_info()
    def request_software_update(self, *, source="manual"):
        if self.ci_status != "success": raise RuntimeError("target CI is not green")
        self.requests.append(source)
        return "req1", self.remote_update_info()
    def request_rollback(self):
        self.requests.append("rollback")
        return "rollback1"
    def claim_update_notification(self, sha, kind="available"):
        key = (sha, kind)
        if key in self.notifications: return False
        self.notifications.add(key); return True
    def bind_update_progress_message(self, request_id, chat_id, message_id, *, target_sha=None):
        self.bindings.append((request_id, chat_id, message_id, target_sha))
        self.binding = SimpleNamespace(request_id=request_id, chat_id=chat_id, message_id=message_id, target_sha=target_sha)
    def update_progress_binding(self): return self.binding
    def clear_update_progress_binding(self, request_id=None): self.binding = None
    def update_history(self, *, limit=10): return ()


def app(*, ci_status="success", available=True):
    api = FakeAPI(); state = FakeState(); services = FakeServices(ci_status=ci_status, available=available)
    return TelegramBotApp(api=api, owner_id=42, services=services, state=state, config=TelegramConfig()), api, services, state


def callback(user_id: int, data: str):
    return {
        "id": "cb1", "from": {"id": user_id}, "data": data,
        "message": {"message_id": 9, "chat": {"id": user_id, "type": "private"}},
    }


def buttons(message):
    return [button for row in message[2]["reply_markup"]["inline_keyboard"] for button in row]


def test_admin_home_is_compact_persian_hierarchy():
    bot, api, _, _ = app()
    bot.admin.show_home(42)
    text = api.sent[-1][1]
    labels = [button["text"] for button in buttons(api.sent[-1])]
    assert "مرکز مدیریت" in text
    assert labels == [
        "🔄 به‌روزرسانی", "❤️ سلامت سیستم", "🤖 هوش مصنوعی", "📚 آرشیو و ایندکس",
        "👥 دسترسی کاربران", "🧰 ابزارها",
    ]
    visible = text + "\n" + "\n".join(labels)
    for legacy in ("API Key", "Rollback", "Cache", "Health", "Errors"):
        assert legacy not in visible


def test_non_owner_cannot_use_admin_callbacks():
    bot, api, services, _ = app()
    bot._handle_callback(callback(7, "adm:update:install"))
    assert services.requests == []
    assert "فقط برای مالک" in api.sent[-1][1]


def test_update_center_only_shows_install_for_green_exact_sha():
    green, api_green, _, _ = app(ci_status="success")
    green.admin.show_update(42, refresh=True)
    green_callbacks = {button["callback_data"] for button in buttons(api_green.sent[-1])}
    assert "adm:update:install" in green_callbacks
    assert "تست‌های GitHub موفق" in api_green.sent[-1][1]

    pending, api_pending, _, _ = app(ci_status="pending")
    pending.admin.show_update(42, refresh=True)
    pending_callbacks = {button["callback_data"] for button in buttons(api_pending.sent[-1])}
    assert "adm:update:install" not in pending_callbacks
    assert "در حال اجرا" in api_pending.sent[-1][1]


def test_update_mode_defaults_safe_and_auto_requires_confirmation():
    bot, api, services, _ = app()
    bot.admin.show_update_settings(42)
    assert "فقط اطلاع‌رسانی" in api.sent[-1][1]
    bot._handle_callback(callback(42, "adm:update:mode:auto"))
    assert services.mode == "notify"
    assert "فعال‌سازی نصب خودکار" in api.edited[-1][2]
    bot._handle_callback(callback(42, "adm:update:mode:auto:yes"))
    assert services.mode == "auto"


def test_manual_install_binds_same_telegram_message_for_live_progress():
    bot, api, services, _ = app()
    bot._handle_callback(callback(42, "adm:update:install"))
    assert services.requests == ["manual"]
    assert services.bindings == [("req1", 42, 9, "b" * 40)]
    assert api.edited[-1][1] == 9
    assert "به‌روزرسانی DrJavanBot" in api.edited[-1][2]


def test_live_progress_edits_bound_message_and_clears_on_terminal_state():
    bot, api, services, _ = app()
    services.binding = SimpleNamespace(request_id="req1", chat_id=42, message_id=99, target_sha="b" * 40)
    services.status_value = UpdateStatus(
        state="preparing", request_id="req1", target_sha="b" * 40,
        stage="preparing", stage_label="آماده‌سازی نسخه", progress_current=3, progress_total=7,
        message="candidate در حال آماده‌سازی است.",
    )
    assert bot.sync_update_progress() is True
    assert api.edited[-1][1] == 99
    assert "مرحله 3 از 7" in api.edited[-1][2]
    assert services.binding is not None

    services.status_value = UpdateStatus(
        state="success", request_id="req1", target_sha="b" * 40,
        stage="done", stage_label="پایان", progress_current=7, progress_total=7,
        message="نسخه جدید فعال شد.", duration_seconds=12.5,
    )
    assert bot.sync_update_progress() is True
    assert services.binding is None
    assert "زمان عملیات" in api.edited[-1][2]


def test_auto_mode_starts_verified_update_once_and_dedupes_notify_mode():
    bot, api, services, _ = app()
    services.mode = "auto"
    assert bot.notify_update_if_available(force=False) is True
    assert services.requests == ["auto"]
    assert "به‌روزرسانی خودکار آغاز شد" in api.sent[-1][1]

    bot2, api2, services2, _ = app()
    services2.mode = "notify"
    assert bot2.notify_update_if_available(force=False) is True
    assert bot2.notify_update_if_available(force=False) is False
    assert len(api2.sent) == 1


def test_legacy_update_callback_routes_to_new_center():
    bot, api, _, _ = app()
    bot._handle_callback(callback(42, "software_update"))
    assert api.edited
    assert "به‌روزرسانی نرم‌افزار" in api.edited[-1][2]
