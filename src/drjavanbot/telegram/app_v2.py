from __future__ import annotations

from .admin_ui import AdminControlCenter
from .app import TelegramBotApp as CoreTelegramBotApp
from .rendering import inline_keyboard


class TelegramBotApp(CoreTelegramBotApp):
    """Stable question handler plus the product-grade owner control center."""

    def __init__(self, *, api, owner_id, services, state, config) -> None:
        super().__init__(api=api, owner_id=owner_id, services=services, state=state, config=config)
        if hasattr(self.api, "rich_ui_enabled"):
            self.api.rich_ui_enabled = bool(getattr(config, "rich_ui_enabled", True))
        self.admin = AdminControlCenter(self)

    def _handle_command(self, chat_id: int, user_id: int, is_private: bool, text: str) -> None:
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
        if user_id == self.owner_id and is_private:
            if command == "/start":
                self.admin.show_home(chat_id)
                self.admin.check_for_updates(force=True)
                return
            if command in {"/panel", "/settings"}:
                self.admin.show_home(chat_id)
                return
            if command == "/update":
                self.admin.show_update(chat_id, refresh=True)
                return
            if command == "/errors":
                self.admin.show_errors(chat_id)
                return
            if command == "/health":
                self.admin.show_health(chat_id)
                return
            if command == "/stats":
                self.admin.show_stats(chat_id)
                return
        super()._handle_command(chat_id, user_id, is_private, text)

    def _handle_callback(self, cb: dict) -> None:
        data = str(cb.get("data") or "")
        if self.admin.handles_callback(data):
            self.admin.handle_callback(cb)
            return
        super()._handle_callback(cb)

    # Compatibility entry points used by older internal callers/tests.
    def _show_settings(self, chat_id: int, message_id: int | None = None) -> None:
        self.admin.show_home(chat_id, message_id)

    def _show_update(self, chat_id: int, message_id: int | None = None) -> None:
        self.admin.show_update(chat_id, message_id, refresh=True)

    def _show_recent_errors(self, chat_id: int, message_id: int | None = None) -> None:
        self.admin.show_errors(chat_id, message_id)

    def notify_update_if_available(self, *, force: bool = False) -> bool:
        return self.admin.check_for_updates(force=force)

    def sync_update_progress(self) -> bool:
        return self.admin.sync_update_progress()

    def _owner_home_keyboard(self) -> dict:
        return inline_keyboard([
            [("🔄 به‌روزرسانی", "adm:update"), ("❤️ سلامت سیستم", "adm:health")],
            [("🤖 هوش مصنوعی", "adm:ai"), ("📚 آرشیو و ایندکس", "adm:archive")],
            [("👥 دسترسی کاربران", "adm:access"), ("🧰 ابزارها", "adm:tools")],
        ])

    def _help_text(self, user_id: int) -> str:
        text = super()._help_text(user_id)
        if user_id == self.owner_id:
            if "/panel" not in text: text += "\n/panel — مرکز مدیریت"
            if "/update" not in text: text += "\n/update — به‌روزرسانی نرم‌افزار"
            if "/errors" not in text: text += "\n/errors — خطاهای اخیر"
        return text
