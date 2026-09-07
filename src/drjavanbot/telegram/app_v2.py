from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from .app import TelegramBotApp as CoreTelegramBotApp
from .rendering import RichScreen, html_escape, inline_keyboard, rich_text

_UPDATE_CALLBACKS = {
    "software_update",
    "software_update_confirm",
    "software_rollback",
    "software_rollback_confirm",
    "recent_errors",
}


class TelegramBotApp(CoreTelegramBotApp):
    """Owner updater/diagnostics UI layered over the stable Telegram handler."""

    def __init__(self, *, api, owner_id, services, state, config) -> None:
        super().__init__(api=api, owner_id=owner_id, services=services, state=state, config=config)
        # One presentation rollback flag; business behavior is unaffected.
        if hasattr(self.api, "rich_ui_enabled"):
            self.api.rich_ui_enabled = bool(getattr(config, "rich_ui_enabled", True))

    def _owner_home_keyboard(self) -> dict:
        return inline_keyboard([
            [("⚙️ پنل مالک", "settings"), ("🔄 به‌روزرسانی", "software_update")],
            [("🧯 خطاهای اخیر", "recent_errors")],
        ])

    def _handle_command(self, chat_id: int, user_id: int, is_private: bool, text: str) -> None:
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
        if command == "/start" and user_id == self.owner_id and is_private:
            screen = RichScreen(
                rich_html=(
                    "<h3>🦷 DrJavanBot</h3>"
                    "<p>پاسخ‌ها فقط از پیام‌های آرشیو گروه ساخته می‌شوند.</p>"
                    "<blockquote>مالک شناسایی شد. تنظیمات، سلامت سیستم و به‌روزرسانی امن از پنل زیر در دسترس است.</blockquote>"
                    "<footer>مدل AI منبع پاسخ نیست؛ فقط برای جست‌وجو و خلاصه‌سازی شواهد گروه استفاده می‌شود.</footer>"
                ),
                fallback_html=(
                    "🦷 <b>DrJavanBot</b>\n"
                    "پاسخ‌ها فقط از پیام‌های آرشیو گروه ساخته می‌شوند.\n\n"
                    "مالک شناسایی شد؛ پنل مدیریت از دکمه‌های زیر در دسترس است."
                ),
                screen_id="owner_home",
            )
            self.api.send_message(chat_id, rich_text(screen), reply_markup=self._owner_home_keyboard())
            self.notify_update_if_available(force=True)
            return
        if command in {"/panel", "/settings"}:
            if not self._require_owner_private(chat_id, user_id, is_private):
                return
            self._show_settings(chat_id)
            return
        if command == "/help" and user_id == self.owner_id and is_private:
            self.api.send_message(chat_id, self._help_text(user_id), reply_markup=self._owner_home_keyboard())
            return
        if command == "/update":
            if not self._require_owner_private(chat_id, user_id, is_private):
                return
            self._show_update(chat_id)
            return
        if command == "/errors":
            if not self._require_owner_private(chat_id, user_id, is_private):
                return
            self._show_recent_errors(chat_id)
            return
        super()._handle_command(chat_id, user_id, is_private, text)

    def notify_update_if_available(self, *, force: bool = False) -> bool:
        remote = getattr(self.services, "remote_update_info", None)
        claim = getattr(self.services, "claim_update_notification", None)
        if not callable(remote):
            return False
        try:
            info = remote()
        except Exception:
            return False
        if info is None or not getattr(info, "update_available", False):
            return False
        latest = str(getattr(info, "latest_sha", "") or "")
        current = str(getattr(info, "current_sha", "") or "")
        if not latest:
            return False
        if not force and callable(claim):
            try:
                if not claim(latest):
                    return False
            except Exception:
                return False
        elif force and callable(claim):
            try:
                claim(latest)
            except Exception:
                pass
        fallback = (
            "🆕 <b>نسخه جدید DrJavanBot موجود است</b>\n"
            f"نسخه فعلی: <code>{html_escape(current[:12] if current else 'unknown')}</code>\n"
            f"آخرین main: <code>{html_escape(latest[:12])}</code>\n\n"
            "نصب بدون تأیید انجام نمی‌شود؛ candidate ابتدا کامل تست می‌شود."
        )
        screen = RichScreen(
            rich_html=(
                "<h3>🆕 نسخه جدید DrJavanBot</h3>"
                "<ul>"
                f"<li>نسخه فعلی: {html_escape(current[:12] if current else 'unknown')}</li>"
                f"<li>آخرین main: {html_escape(latest[:12])}</li>"
                "</ul>"
                "<blockquote>نصب خودکار انجام نمی‌شود. با تأیید شما، candidate ابتدا تست و staging می‌شود و فقط در صورت موفقیت جایگزین نسخه فعال خواهد شد.</blockquote>"
            ),
            fallback_html=fallback,
            screen_id="update_available",
        )
        self.api.send_message(
            self.owner_id,
            rich_text(screen),
            reply_markup=inline_keyboard([
                [("🧪 تست و نصب", "software_update_confirm")],
                [("📋 وضعیت آپدیتر", "software_update")],
            ]),
        )
        return True

    def _handle_callback(self, cb: dict) -> None:
        data = str(cb.get("data") or "")
        if data not in _UPDATE_CALLBACKS:
            super()._handle_callback(cb)
            return

        user = cb.get("from") or {}
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        try:
            user_id = int(user.get("id"))
            chat_id = int(chat.get("id"))
            message_id = int(msg.get("message_id"))
        except (TypeError, ValueError):
            return
        try:
            self.api.answer_callback(str(cb.get("id") or ""))
        except Exception:
            pass
        if user_id != self.owner_id or chat.get("type") != "private":
            self.api.send_message(chat_id, "⛔️ این عملیات فقط برای مالک و در گفت‌وگوی خصوصی مجاز است.")
            return

        if data == "recent_errors":
            self._show_recent_errors(chat_id, message_id)
            return
        if data == "software_update":
            self._show_update(chat_id, message_id)
            return
        if data == "software_update_confirm":
            try:
                request_id = self.services.request_software_update()
                text = (
                    "✅ <b>درخواست به‌روزرسانی ثبت شد</b>\n"
                    f"Request: <code>{html_escape(request_id)}</code>\n\n"
                    "candidate در کنار نسخه فعال ساخته و تست می‌شود؛ تا مرحله switch ربات فعلی روشن می‌ماند."
                )
            except RuntimeError:
                text = "⏳ یک درخواست به‌روزرسانی/rollback فعال است. وضعیت را باز کنید؛ درخواست stale پس از timeout قابل جایگزینی است."
            self.api.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=inline_keyboard([[("📋 وضعیت", "software_update")]]),
            )
            return
        if data == "software_rollback":
            self.api.edit_message_text(
                chat_id,
                message_id,
                "↩️ <b>بازگشت نسخه</b>\nبه آخرین release سالم قبلی برگردیم؟",
                reply_markup=inline_keyboard([
                    [("✅ تأیید Rollback", "software_rollback_confirm")],
                    [("انصراف", "software_update")],
                ]),
            )
            return
        if data == "software_rollback_confirm":
            try:
                request_id = self.services.request_rollback()
                text = "✅ درخواست rollback ثبت شد.\nRequest: <code>%s</code>" % html_escape(request_id)
            except RuntimeError:
                text = "⏳ یک درخواست به‌روزرسانی/rollback از قبل فعال است."
            self.api.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=inline_keyboard([[("📋 وضعیت", "software_update")]]),
            )

    def _show_settings(self, chat_id: int, message_id: int | None = None) -> None:
        ai_ok = self.services.ai_configured()
        status = "تنظیم شده ✅" if ai_ok else "تنظیم نشده ❌"
        model = html_escape(self.services.model())
        access = html_escape(self.state.access_mode())
        fallback = (
            "⚙️ <b>پنل مالک</b>\n\n"
            f"AvalAI: {status}\n"
            f"مدل: <code>{model}</code>\n"
            f"دسترسی: <code>{access}</code>\n\n"
            "پاسخ کاربران فقط از شواهد آرشیو گروه ساخته می‌شود."
        )
        screen = RichScreen(
            rich_html=(
                "<h3>⚙️ پنل مالک</h3>"
                "<ul>"
                f"<li>AvalAI: {status}</li>"
                f"<li>مدل: {model}</li>"
                f"<li>دسترسی: {access}</li>"
                f"<li>Rich UI: {'فعال' if getattr(self.config, 'rich_ui_enabled', True) else 'غیرفعال'}</li>"
                "</ul>"
                "<blockquote>AI فقط برنامه‌ریزی جست‌وجو و خلاصه‌سازی شواهد را انجام می‌دهد؛ پاسخ بدون support معتبر از پیام‌های گروه پذیرفته نمی‌شود.</blockquote>"
            ),
            fallback_html=fallback,
            screen_id="owner_settings",
        )
        kb = inline_keyboard([
            [("🔑 API Key", "setkey"), ("🧪 تست AvalAI", "testai")],
            [("🗑 حذف Key", "remove_key"), ("🤖 مدل", "models")],
            [("📊 آمار", "stats"), ("❤️ سلامت", "health")],
            [("🗂 بازسازی ایندکس", "reindex"), ("📚 وضعیت ایندکس", "indexstats")],
            [("🧹 Cache", "cache"), ("👥 دسترسی / Rate", "access")],
            [("🧯 خطاهای اخیر", "recent_errors"), ("🔄 به‌روزرسانی", "software_update")],
        ])
        self._send_or_edit_screen(chat_id, message_id, screen, kb)

    def _show_recent_errors(self, chat_id: int, message_id: int | None = None) -> None:
        rows = []
        try:
            with sqlite3.connect(self.state.path) as con:
                rows = con.execute(
                    "SELECT id,created_at,error_class FROM question_usage "
                    "WHERE success=0 AND error_class IS NOT NULL ORDER BY id DESC LIMIT 8"
                ).fetchall()
        except (sqlite3.Error, OSError):
            rows = []
        fallback_lines = ["🧯 <b>خطاهای اخیر</b>"]
        rich = ["<h3>🧯 خطاهای اخیر</h3>"]
        if not rows:
            fallback_lines.append("خطای ثبت‌شده‌ای وجود ندارد.")
            rich.append("<p>خطای ثبت‌شده‌ای وجود ندارد.</p>")
        else:
            rich.append("<ul>")
            for error_id, created_at, error_class in rows:
                try:
                    stamp = datetime.fromtimestamp(float(created_at), timezone.utc).isoformat(timespec="seconds")
                except (TypeError, ValueError, OSError):
                    stamp = "زمان نامشخص"
                item = f"Q{int(error_id)} — {html_escape(error_class)} — {html_escape(stamp)}"
                fallback_lines.append(item)
                rich.append(f"<li>{item}</li>")
            rich.append("</ul>")
            note = "برای دیباگ فقط Error ID و Class را بفرست؛ متن سؤال یا secret اینجا ذخیره/نمایش داده نمی‌شود."
            fallback_lines.extend(("", note))
            rich.append(f"<footer>{note}</footer>")
        screen = RichScreen("".join(rich), "\n".join(fallback_lines), "recent_errors")
        kb = inline_keyboard([[("🔄 تازه‌سازی", "recent_errors")], [("⬅️ پنل", "settings")]])
        self._send_or_edit_screen(chat_id, message_id, screen, kb)

    def _show_update(self, chat_id: int, message_id: int | None = None) -> None:
        status = self.services.update_status()
        state = str(status.state or "unknown")
        icon = {
            "idle": "🟢",
            "success": "✅",
            "pending": "🟡",
            "queued": "🟡",
            "running": "🔵",
            "stalled": "🟠",
            "failed": "🔴",
        }.get(state.casefold(), "⚪️")
        state_label = {
            "idle": "آماده",
            "success": "موفق",
            "pending": "در صف",
            "queued": "در صف",
            "running": "در حال اجرا",
            "stalled": "بدون heartbeat / نیازمند بررسی",
            "failed": "ناموفق",
        }.get(state.casefold(), state)

        current = html_escape(status.current_sha[:12]) if status.current_sha else "—"
        target = html_escape(status.target_sha[:12]) if status.target_sha else "—"
        action = html_escape(status.action or "—")
        request = html_escape(status.request_id or "—")
        message = html_escape(status.message or "پیامی ثبت نشده است.")
        updated = html_escape(status.updated_at or "—")
        fallback = (
            "🔄 <b>به‌روزرسانی DrJavanBot</b>\n\n"
            f"وضعیت: {icon} {html_escape(state_label)}\n"
            f"نسخه فعلی: <code>{current}</code>\n"
            f"نسخه هدف: <code>{target}</code>\n"
            f"عملیات: <code>{action}</code>\n"
            f"Request: <code>{request}</code>\n\n"
            f"{message}\n"
            f"آخرین بروزرسانی وضعیت: {updated}"
        )
        screen = RichScreen(
            rich_html=(
                "<h3>🔄 به‌روزرسانی DrJavanBot</h3>"
                "<ul>"
                f"<li>وضعیت: {icon} {html_escape(state_label)}</li>"
                f"<li>نسخه فعلی: {current}</li>"
                f"<li>نسخه هدف: {target}</li>"
                f"<li>عملیات: {action}</li>"
                f"<li>Request: {request}</li>"
                "</ul>"
                f"<blockquote>{message}</blockquote>"
                f"<footer>آخرین بروزرسانی وضعیت: {updated}</footer>"
            ),
            fallback_html=fallback,
            screen_id="software_update",
        )
        kb = inline_keyboard([
            [("🧪 تست و نصب آخرین main", "software_update_confirm")],
            [("↩️ بازگشت نسخه", "software_rollback")],
            [("🔄 تازه‌سازی وضعیت", "software_update")],
            [("⬅️ پنل مالک", "settings")],
        ])
        self._send_or_edit_screen(chat_id, message_id, screen, kb)

    def _send_or_edit_screen(self, chat_id: int, message_id: int | None, screen: RichScreen, keyboard: dict | None) -> None:
        content = rich_text(screen)
        if message_id is None:
            self.api.send_message(chat_id, content, reply_markup=keyboard)
        else:
            self.api.edit_message_text(chat_id, message_id, content, reply_markup=keyboard)

    def _help_text(self, user_id: int) -> str:
        text = super()._help_text(user_id)
        if user_id == self.owner_id:
            if "/panel" not in text:
                text += "\n/panel — پنل مالک"
            if "/update" not in text:
                text += "\n/update — به‌روزرسانی امن نرم‌افزار"
            if "/errors" not in text:
                text += "\n/errors — خطاهای اخیر بدون جزئیات حساس"
        return text
