from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from .app import TelegramBotApp as CoreTelegramBotApp
from .rendering import html_escape, inline_keyboard

_UPDATE_CALLBACKS = {
    "software_update",
    "software_update_confirm",
    "software_rollback",
    "software_rollback_confirm",
    "recent_errors",
}


class TelegramBotApp(CoreTelegramBotApp):
    """Owner updater/diagnostics UI layered over the stable Telegram handler."""

    def _owner_home_keyboard(self) -> dict:
        return inline_keyboard([
            [("⚙️ پنل مالک", "settings"), ("🔄 Software Update", "software_update")],
            [("🧯 خطاهای اخیر", "recent_errors")],
        ])

    def _handle_command(self, chat_id: int, user_id: int, is_private: bool, text: str) -> None:
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
        if command == "/start" and user_id == self.owner_id and is_private:
            self.api.send_message(
                chat_id,
                "🦷 <b>DrJavanBot</b>\nسؤال را بفرستید؛ پاسخ فقط بر پایه آرشیو گروه تولید می‌شود.\n\nمالک شناسایی شد؛ پنل مدیریت از دکمه زیر در دسترس است.",
                reply_markup=self._owner_home_keyboard(),
            )
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
                    "✅ درخواست آپدیت ثبت شد.\n"
                    f"Request: <code>{html_escape(request_id)}</code>\n"
                    "Updater جداگانه GitHub را fetch می‌کند، تست‌ها را اجرا می‌کند و فقط در صورت موفقیت نسخه را جایگزین می‌کند."
                )
            except RuntimeError:
                text = "⏳ یک درخواست آپدیت/rollback از قبل در صف است."
            self.api.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=inline_keyboard([[("وضعیت", "software_update")]]),
            )
            return
        if data == "software_rollback":
            self.api.edit_message_text(
                chat_id,
                message_id,
                "↩️ بازگشت به آخرین release سالم قبلی؟",
                reply_markup=inline_keyboard([
                    [("✅ Rollback", "software_rollback_confirm")],
                    [("انصراف", "software_update")],
                ]),
            )
            return
        if data == "software_rollback_confirm":
            try:
                request_id = self.services.request_rollback()
                text = "✅ درخواست rollback ثبت شد.\nRequest: <code>%s</code>" % html_escape(request_id)
            except RuntimeError:
                text = "⏳ یک درخواست آپدیت/rollback از قبل در صف است."
            self.api.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=inline_keyboard([[("وضعیت", "software_update")]]),
            )

    def _show_settings(self, chat_id: int, message_id: int | None = None) -> None:
        status = "✅ تنظیم شده" if self.services.ai_configured() else "❌ تنظیم نشده"
        text = (
            "<b>تنظیمات مالک</b>\n"
            f"AvalAI: {status}\n"
            f"Model: <code>{html_escape(self.services.model())}</code>\n"
            f"Access: <code>{self.state.access_mode()}</code>"
        )
        kb = inline_keyboard([
            [("🔑 تنظیم/تعویض API Key", "setkey"), ("🧪 تست AvalAI", "testai")],
            [("🗑 حذف API Key", "remove_key"), ("🤖 مدل", "models")],
            [("📊 آمار", "stats"), ("❤️ Health", "health")],
            [("🗂 Reindex", "reindex"), ("📚 Index", "indexstats")],
            [("🧹 Cache", "cache"), ("👥 دسترسی/Rate", "access")],
            [("🧯 خطاهای اخیر", "recent_errors"), ("🔄 Software Update", "software_update")],
        ])
        if message_id is None:
            self.api.send_message(chat_id, text, reply_markup=kb)
        else:
            self.api.edit_message_text(chat_id, message_id, text, reply_markup=kb)

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
        lines = ["<b>خطاهای اخیر</b>"]
        if not rows:
            lines.append("خطای ثبت‌شده‌ای وجود ندارد.")
        else:
            for error_id, created_at, error_class in rows:
                try:
                    stamp = datetime.fromtimestamp(float(created_at), timezone.utc).isoformat(timespec="seconds")
                except (TypeError, ValueError, OSError):
                    stamp = "زمان نامشخص"
                lines.append(
                    f"<code>Q{int(error_id)}</code> — <code>{html_escape(error_class)}</code> — {html_escape(stamp)}"
                )
            lines.append("\nبرای دیباگ فقط Error ID و Class را بفرست؛ متن سؤال یا secret اینجا ذخیره/نمایش داده نمی‌شود.")
        kb = inline_keyboard([[("🔄 تازه‌سازی", "recent_errors")], [("بازگشت", "settings")]])
        text = "\n".join(lines)
        if message_id is None:
            self.api.send_message(chat_id, text, reply_markup=kb)
        else:
            self.api.edit_message_text(chat_id, message_id, text, reply_markup=kb)

    def _show_update(self, chat_id: int, message_id: int | None = None) -> None:
        status = self.services.update_status()
        lines = ["<b>Software Update</b>", f"State: <code>{html_escape(status.state)}</code>"]
        if status.request_id:
            lines.append(f"Request: <code>{html_escape(status.request_id)}</code>")
        if status.action:
            lines.append(f"Action: <code>{html_escape(status.action)}</code>")
        if status.current_sha:
            lines.append(f"Current: <code>{html_escape(status.current_sha[:12])}</code>")
        if status.target_sha:
            lines.append(f"Target: <code>{html_escape(status.target_sha[:12])}</code>")
        if status.message:
            lines.append(html_escape(status.message))
        if status.updated_at:
            lines.append(f"Updated: {html_escape(status.updated_at)}")
        kb = inline_keyboard([
            [("🧪 تست و نصب آخرین main", "software_update_confirm")],
            [("↩️ Rollback", "software_rollback")],
            [("🔄 تازه‌سازی وضعیت", "software_update")],
            [("بازگشت", "settings")],
        ])
        text = "\n".join(lines)
        if message_id is None:
            self.api.send_message(chat_id, text, reply_markup=kb)
        else:
            self.api.edit_message_text(chat_id, message_id, text, reply_markup=kb)

    def _help_text(self, user_id: int) -> str:
        text = super()._help_text(user_id)
        if user_id == self.owner_id:
            if "/panel" not in text:
                text += "\n/panel — پنل مالک"
            if "/update" not in text:
                text += "\n/update — آپدیت امن نرم‌افزار"
            if "/errors" not in text:
                text += "\n/errors — خطاهای اخیر بدون جزئیات حساس"
        return text
