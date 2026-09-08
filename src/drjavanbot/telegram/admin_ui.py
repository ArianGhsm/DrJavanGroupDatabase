from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from .api import TelegramAPIError
from .config import ALLOWED_MODELS
from .rendering import html_escape, inline_keyboard
from .update_control import UPDATE_MODE_AUTO, UPDATE_MODE_NOTIFY, UPDATE_MODE_OFF, UpdateStatus

_LOG = logging.getLogger(__name__)


class V:
    """Centralized Persian vocabulary for the owner-facing control center."""

    HOME = "مرکز مدیریت"
    UPDATE = "به‌روزرسانی نرم‌افزار"
    HEALTH = "سلامت سیستم"
    AI = "هوش مصنوعی"
    ARCHIVE = "آرشیو و ایندکس"
    ACCESS = "دسترسی کاربران"
    TOOLS = "ابزارها و عیب‌یابی"
    ERRORS = "خطاهای اخیر"
    HISTORY = "سابقه نسخه‌ها"
    BACK = "بازگشت"
    REFRESH = "تازه‌سازی"
    INSTALL = "نصب نسخه جدید"
    ROLLBACK = "بازگشت به نسخه قبلی"
    UPDATE_SETTINGS = "تنظیمات به‌روزرسانی"


_STATE_META = {
    "idle": ("🟢", "آماده"),
    "pending": ("🟡", "در صف"),
    "queued": ("🟡", "در صف"),
    "checking": ("🔵", "در حال بررسی"),
    "preparing": ("🔵", "در حال آماده‌سازی"),
    "validating": ("🔵", "در حال اعتبارسنجی"),
    "staging": ("🔵", "در حال آماده‌سازی نسخه"),
    "switching": ("🔵", "در حال فعال‌سازی"),
    "restarting": ("🔵", "در حال راه‌اندازی"),
    "verifying": ("🔵", "در حال بررسی سلامت"),
    "running": ("🔵", "در حال اجرا"),
    "success": ("🟢", "موفق"),
    "up_to_date": ("🟢", "به‌روز"),
    "rolling_back": ("🔵", "در حال بازگشت"),
    "rolled_back": ("🟢", "بازگشت انجام شد"),
    "stalled": ("🟡", "نیازمند بررسی"),
    "failed": ("🔴", "ناموفق"),
    "unknown": ("⚪", "نامشخص"),
}

_CI_META = {
    "success": ("🟢", "تست‌های GitHub موفق"),
    "pending": ("🟡", "تست‌های GitHub در حال اجرا"),
    "failure": ("🔴", "تست‌های GitHub ناموفق"),
    "unknown": ("🟡", "وضعیت GitHub فعلاً نامشخص"),
}

_MODE_LABELS = {
    UPDATE_MODE_NOTIFY: "فقط اطلاع‌رسانی",
    UPDATE_MODE_AUTO: "نصب خودکار نسخه تأییدشده",
    UPDATE_MODE_OFF: "غیرفعال",
}

_PROGRESS_STAGES = (
    "بررسی نسخه",
    "بررسی تست‌های GitHub",
    "آماده‌سازی نسخه",
    "اعتبارسنجی محیط اجرا",
    "فعال‌سازی",
    "بررسی سلامت و تلگرام",
    "پایان",
)


class AdminControlCenter:
    """Owner-only product UI. No arbitrary shell or filesystem input is exposed."""

    PREFIX = "adm:"
    LEGACY_CALLBACKS = {
        "settings", "software_update", "software_update_confirm",
        "software_rollback", "software_rollback_confirm", "recent_errors",
    }

    def __init__(self, app) -> None:
        self.app = app
        self.api = app.api
        self.owner_id = app.owner_id
        self.services = app.services
        self.state = app.state
        self.config = app.config

    def handles_callback(self, data: str) -> bool:
        return data.startswith(self.PREFIX) or data in self.LEGACY_CALLBACKS

    def handle_callback(self, cb: dict) -> None:
        data = str(cb.get("data") or "")
        user = cb.get("from") or {}
        message = cb.get("message") or {}
        chat = message.get("chat") or {}
        try:
            user_id = int(user.get("id"))
            chat_id = int(chat.get("id"))
            message_id = int(message.get("message_id"))
        except (TypeError, ValueError):
            return
        try:
            self.api.answer_callback(str(cb.get("id") or ""))
        except Exception:
            pass
        if user_id != self.owner_id or chat.get("type") != "private":
            self.api.send_message(chat_id, "⛔️ این بخش فقط برای مالک ربات در گفت‌وگوی خصوصی فعال است.")
            return

        data = {
            "settings": "adm:home",
            "software_update": "adm:update",
            "software_update_confirm": "adm:update:install",
            "software_rollback": "adm:update:rollback",
            "software_rollback_confirm": "adm:update:rollback:yes",
            "recent_errors": "adm:errors",
        }.get(data, data)

        routes = {
            "adm:home": lambda: self.show_home(chat_id, message_id),
            "adm:update": lambda: self.show_update(chat_id, message_id, refresh=True),
            "adm:update:install": lambda: self._install(chat_id, message_id),
            "adm:update:settings": lambda: self.show_update_settings(chat_id, message_id),
            "adm:update:history": lambda: self.show_update_history(chat_id, message_id),
            "adm:update:rollback": lambda: self._confirm_rollback(chat_id, message_id),
            "adm:update:rollback:yes": lambda: self._rollback(chat_id, message_id),
            "adm:update:error": lambda: self.show_update_error(chat_id, message_id),
            "adm:health": lambda: self.show_health(chat_id, message_id),
            "adm:ai": lambda: self.show_ai(chat_id, message_id),
            "adm:ai:test": lambda: self._test_ai(chat_id, message_id),
            "adm:ai:remove": lambda: self._confirm_remove_key(chat_id, message_id),
            "adm:ai:remove:yes": lambda: self._remove_key(chat_id, message_id),
            "adm:ai:models": lambda: self._show_models(chat_id, message_id),
            "adm:archive": lambda: self.show_archive(chat_id, message_id),
            "adm:archive:reindex": lambda: self._confirm_reindex(chat_id, message_id),
            "adm:archive:reindex:yes": lambda: self.app._start_reindex(chat_id),
            "adm:access": lambda: self.show_access(chat_id, message_id),
            "adm:tools": lambda: self.show_tools(chat_id, message_id),
            "adm:errors": lambda: self.show_errors(chat_id, message_id),
            "adm:cache": lambda: self.show_cache(chat_id, message_id),
            "adm:cache:clear": lambda: self._clear_cache(chat_id, message_id),
            "adm:stats": lambda: self.show_stats(chat_id, message_id),
        }
        route = routes.get(data)
        if route is not None:
            route()
            return
        if data == "adm:update:mode:notify":
            self._set_update_mode(UPDATE_MODE_NOTIFY); self.show_update_settings(chat_id, message_id); return
        if data == "adm:update:mode:off":
            self._set_update_mode(UPDATE_MODE_OFF); self.show_update_settings(chat_id, message_id); return
        if data == "adm:update:mode:auto":
            self._confirm_auto_mode(chat_id, message_id); return
        if data == "adm:update:mode:auto:yes":
            self._set_update_mode(UPDATE_MODE_AUTO); self.show_update_settings(chat_id, message_id); return
        if data == "adm:ai:key":
            self.state.begin_flow(user_id, "await_avalai_key", self.config.key_entry_timeout_seconds)
            self.api.send_message(chat_id, "🔑 کلید API را همین‌جا بفرستید. پیام حاوی کلید پس از دریافت حذف می‌شود.\n/cancel برای لغو.")
            return
        if data.startswith("adm:ai:model:"):
            self._set_model(chat_id, message_id, data.split(":", 3)[-1]); return
        if data.startswith("adm:access:mode:"):
            self._set_access_mode(data.rsplit(":", 1)[-1]); self.show_access(chat_id, message_id); return
        if data.startswith("adm:access:rate:"):
            self._set_rate(data.rsplit(":", 1)[-1]); self.show_access(chat_id, message_id); return

    def show_home(self, chat_id: int, message_id: int | None = None) -> None:
        health = self._safe_health()
        status = self._status()
        remote = self._cached_remote()
        index_ok = _index_ok(health.get("index"))
        ai_ok = bool(health.get("ai_configured")) and not bool(health.get("provider_auth_failed"))
        if status.active:
            update_line = "🔵 در حال اجرا"
        elif remote is not None and getattr(remote, "update_available", False):
            update_line = "🟡 نسخه جدید موجود است"
        elif remote is not None:
            update_line = "🟢 به‌روز"
        else:
            update_line = "⚪ در انتظار بررسی"
        overall = "🟢 سالم" if index_ok and ai_ok and status.state != "failed" else "🟡 نیازمند توجه"
        text = (
            "🦷 <b>مرکز مدیریت DrJavanBot</b>\n"
            f"وضعیت کلی: {overall}\n\n"
            f"نسخه: <code>{_short(status.current_sha or self._current_sha())}</code>\n"
            f"ایندکس: {'🟢 آماده' if index_ok else '🟡 نیازمند بررسی'}\n"
            f"هوش مصنوعی: {'🟢 متصل' if ai_ok else '🟡 نیازمند بررسی'}\n"
            f"به‌روزرسانی: {update_line}"
        )
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("🔄 به‌روزرسانی", "adm:update"), ("❤️ سلامت سیستم", "adm:health")],
            [("🤖 هوش مصنوعی", "adm:ai"), ("📚 آرشیو و ایندکس", "adm:archive")],
            [("👥 دسترسی کاربران", "adm:access"), ("🧰 ابزارها", "adm:tools")],
        ]))

    def show_update(self, chat_id: int, message_id: int | None = None, *, refresh: bool = False) -> None:
        status = self._status()
        if status.active:
            self._send_or_edit(chat_id, message_id, self._progress_text(status), self._progress_keyboard(status))
            return
        cached = self._cached_remote()
        remote = self._remote() if refresh or cached is None else cached
        current = status.current_sha or self._current_sha()
        previous = self._previous_sha()
        mode = self._update_mode()
        lines = [
            "🔄 <b>به‌روزرسانی نرم‌افزار</b>", "",
            f"نسخه نصب‌شده: <code>{_short(current)}</code>",
        ]
        installable = False
        if remote is None:
            lines += ["آخرین نسخه: —", "", "🟡 ارتباط با GitHub فعلاً قابل بررسی نیست."]
        else:
            latest = getattr(remote, "latest_sha", None)
            lines.append(f"آخرین نسخه: <code>{_short(latest)}</code>")
            ci_status = str(getattr(remote, "ci_status", "unknown") or "unknown")
            ci_icon, ci_label = _CI_META.get(ci_status, _CI_META["unknown"])
            if getattr(remote, "update_available", False):
                lines += ["", "🟡 نسخه جدید موجود است.", f"{ci_icon} {ci_label}"]
                installable = ci_status == "success"
            else:
                lines += ["", "🟢 ربات به‌روز است.", f"{ci_icon} {ci_label}"]
        lines += [
            "",
            f"حالت به‌روزرسانی: <b>{html_escape(_MODE_LABELS.get(mode, mode))}</b>",
            f"بازگشت به نسخه قبلی: {'🟢 آماده' if previous else '⚪ در دسترس نیست'}",
        ]
        last = self._history(limit=1)
        if last:
            stamp = _event_time(last[0])
            lines.append(f"آخرین عملیات: {_history_result(last[0])}{(' — ' + stamp) if stamp else ''}")
        rows: list[list[tuple[str, str]]] = []
        if installable:
            rows.append([("⬆️ نصب نسخه جدید", "adm:update:install")])
        rows.append([("⚙️ تنظیمات", "adm:update:settings"), ("🕘 سابقه نسخه‌ها", "adm:update:history")])
        second = [("🔄 تازه‌سازی", "adm:update")]
        if previous:
            second.insert(0, ("↩️ نسخه قبلی", "adm:update:rollback"))
        rows += [second, [("⬅️ مرکز مدیریت", "adm:home")]]
        self._send_or_edit(chat_id, message_id, "\n".join(lines), inline_keyboard(rows))

    def show_update_settings(self, chat_id: int, message_id: int | None = None) -> None:
        mode = self._update_mode()
        text = (
            "⚙️ <b>تنظیمات به‌روزرسانی</b>\n\n"
            f"حالت فعلی: <b>{html_escape(_MODE_LABELS.get(mode, mode))}</b>\n\n"
            "• فقط اطلاع‌رسانی: نسخه جدید را اعلام می‌کند و نصب با شماست.\n"
            "• نصب خودکار: فقط همان نسخه‌ای نصب می‌شود که تست‌های GitHub آن موفق باشند.\n"
            "• غیرفعال: بررسی دوره‌ای نسخه جدید متوقف می‌شود."
        )
        def label(value: str, title: str) -> str:
            return ("✅ " if mode == value else "") + title
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [(label(UPDATE_MODE_NOTIFY, "فقط اطلاع‌رسانی"), "adm:update:mode:notify")],
            [(label(UPDATE_MODE_AUTO, "نصب خودکار تأییدشده"), "adm:update:mode:auto")],
            [(label(UPDATE_MODE_OFF, "غیرفعال"), "adm:update:mode:off")],
            [("⬅️ به‌روزرسانی", "adm:update")],
        ]))

    def show_update_history(self, chat_id: int, message_id: int | None = None) -> None:
        items = self._history(limit=10)
        lines = ["🕘 <b>سابقه نسخه‌ها</b>"]
        if not items:
            lines += ["", "هنوز سابقه‌ای ثبت نشده است."]
        else:
            lines.append("")
            for item in items:
                stamp = _event_time(item)
                duration = item.get("duration_seconds")
                duration_text = f" — {int(float(duration))} ثانیه" if duration is not None else ""
                lines.append(
                    f"{_history_result(item)} — <code>{_short(item.get('target_sha'))}</code>"
                    f"{(' — ' + stamp) if stamp else ''}{duration_text}"
                )
        self._send_or_edit(chat_id, message_id, "\n".join(lines), inline_keyboard([
            [("🔄 تازه‌سازی", "adm:update:history")], [("⬅️ به‌روزرسانی", "adm:update")],
        ]))

    def show_update_error(self, chat_id: int, message_id: int | None = None) -> None:
        status = self._status()
        if status.state != "failed":
            self.show_update(chat_id, message_id)
            return
        lines = ["🔴 <b>جزئیات خطای به‌روزرسانی</b>", ""]
        if status.error_id:
            lines.append(f"شناسه خطا: <code>{html_escape(status.error_id)}</code>")
        if status.stage_label:
            lines.append(f"مرحله: {html_escape(status.stage_label)}")
        lines.append("نسخه فعال تا حد ممکن حفظ شده است.")
        if status.detail:
            lines += ["", "جزئیات فنیِ پالایش‌شده:", f"<code>{html_escape(str(status.detail)[:450])}</code>"]
        self._send_or_edit(chat_id, message_id, "\n".join(lines), inline_keyboard([
            [("🔄 تلاش دوباره", "adm:update:install")], [("⬅️ به‌روزرسانی", "adm:update")],
        ]))

    def show_health(self, chat_id: int, message_id: int | None = None) -> None:
        health = self._safe_health()
        index_ok = _index_ok(health.get("index"))
        ai_ok = bool(health.get("ai_configured")) and not bool(health.get("provider_auth_failed"))
        updater_state = str((health.get("updater") or {}).get("state") or self._status().state)
        storage = health.get("storage") or {}
        disk_percent = storage.get("used_percent")
        disk_line = f"{float(disk_percent):.1f}٪ استفاده" if isinstance(disk_percent, (int, float)) else "نامشخص"
        text = (
            "❤️ <b>سلامت سیستم</b>\n\n"
            "🟢 سرویس ربات: فعال\n"
            f"{'🟢' if ai_ok else '🟡'} هوش مصنوعی: {'متصل' if ai_ok else 'نیازمند بررسی'}\n"
            f"{'🟢' if index_ok else '🟡'} آرشیو و ایندکس: {'آماده' if index_ok else 'نیازمند بررسی'}\n"
            f"{_state_icon(updater_state)} سامانه به‌روزرسانی: {_state_label(updater_state)}\n"
            f"💾 فضای ذخیره‌سازی: {html_escape(disk_line)}"
        )
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("🔄 تازه‌سازی", "adm:health")], [("🧯 خطاهای اخیر", "adm:errors")], [("⬅️ مرکز مدیریت", "adm:home")],
        ]))

    def show_ai(self, chat_id: int, message_id: int | None = None, *, note: str | None = None) -> None:
        configured = bool(self.services.ai_configured())
        lines = [
            "🤖 <b>هوش مصنوعی</b>", "",
            f"اتصال: {'🟢 تنظیم شده' if configured else '🟡 تنظیم نشده'}",
            f"مدل فعلی: <code>{html_escape(str(self.services.model()))}</code>",
        ]
        if note:
            lines += ["", note]
        self._send_or_edit(chat_id, message_id, "\n".join(lines), inline_keyboard([
            [("🔑 تنظیم کلید API", "adm:ai:key"), ("🧪 تست اتصال", "adm:ai:test")],
            [("🤖 انتخاب مدل", "adm:ai:models"), ("🗑 حذف کلید", "adm:ai:remove")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ]))

    def show_archive(self, chat_id: int, message_id: int | None = None) -> None:
        try:
            stats = self.services.stats()
        except Exception:
            stats = {}
        index = stats.get("index") or {}
        messages = index.get("messages", index.get("message_count", "—"))
        last_reindex = stats.get("last_reindex_at") or "—"
        index_ok = _index_ok((self._safe_health().get("index") or {}))
        text = (
            "📚 <b>آرشیو و ایندکس</b>\n\n"
            f"پیام‌های ایندکس‌شده: <b>{html_escape(str(messages))}</b>\n"
            f"آخرین بازسازی: {html_escape(str(last_reindex))}\n"
            f"وضعیت: {'🟢 آماده' if index_ok else '🟡 نیازمند بررسی'}"
        )
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("🔄 بازسازی ایندکس", "adm:archive:reindex")], [("⬅️ مرکز مدیریت", "adm:home")],
        ]))

    def show_access(self, chat_id: int, message_id: int | None = None) -> None:
        mode = str(self.state.access_mode())
        rate = int(self.state.rate_limit_per_minute())
        mode_label = {"owner_only": "فقط مالک", "allowlist": "فهرست مجاز", "public": "عمومی"}.get(mode, mode)
        text = (
            "👥 <b>دسترسی کاربران</b>\n\n"
            f"حالت: <b>{html_escape(mode_label)}</b>\n"
            f"محدودیت درخواست: <b>{rate}</b> در دقیقه"
        )
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("فقط مالک", "adm:access:mode:owner_only"), ("فهرست مجاز", "adm:access:mode:allowlist")],
            [("عمومی", "adm:access:mode:public")],
            [("۳ در دقیقه", "adm:access:rate:3"), ("۶ در دقیقه", "adm:access:rate:6"), ("۱۲ در دقیقه", "adm:access:rate:12")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ]))

    def show_tools(self, chat_id: int, message_id: int | None = None) -> None:
        self._send_or_edit(
            chat_id, message_id,
            "🧰 <b>ابزارها و عیب‌یابی</b>\n\nابزارهای کم‌استفاده اینجا قرار گرفته‌اند تا صفحه اصلی ساده بماند.",
            inline_keyboard([
                [("📊 آمار", "adm:stats"), ("🧹 حافظه موقت", "adm:cache")],
                [("🧯 خطاهای اخیر", "adm:errors")], [("⬅️ مرکز مدیریت", "adm:home")],
            ]),
        )

    def show_errors(self, chat_id: int, message_id: int | None = None) -> None:
        rows = []
        try:
            import sqlite3
            with sqlite3.connect(self.state.path) as con:
                rows = con.execute(
                    "SELECT id,created_at,error_class FROM question_usage "
                    "WHERE success=0 AND error_class IS NOT NULL ORDER BY id DESC LIMIT 8"
                ).fetchall()
        except Exception:
            rows = []
        lines = ["🧯 <b>خطاهای اخیر</b>"]
        if not rows:
            lines += ["", "🟢 خطای ثبت‌شده‌ای وجود ندارد."]
        else:
            lines.append("")
            for error_id, created_at, error_class in rows:
                try:
                    stamp = datetime.fromtimestamp(float(created_at), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                except Exception:
                    stamp = "زمان نامشخص"
                lines.append(f"🔴 Q{int(error_id)} — <code>{html_escape(str(error_class))}</code> — {stamp}")
            lines += ["", "برای بررسی، شناسه و نوع خطا کافی است؛ متن سؤال و اطلاعات محرمانه نمایش داده نمی‌شوند."]
        self._send_or_edit(chat_id, message_id, "\n".join(lines), inline_keyboard([
            [("🔄 تازه‌سازی", "adm:errors")], [("⬅️ ابزارها", "adm:tools")],
        ]))

    def show_cache(self, chat_id: int, message_id: int | None = None) -> None:
        try:
            cache = self.services.cache.stats()
            text = (
                "🧹 <b>حافظه موقت</b>\n\n"
                f"ورودی‌ها: {int(cache.entries)}\n"
                f"استفاده موفق: {int(cache.hits)}\n"
                f"عدم تطابق: {int(cache.misses)}\n"
                f"منقضی‌شده: {int(cache.expired_entries)}"
            )
        except Exception:
            text = "🧹 <b>حافظه موقت</b>\n\n🟡 آمار فعلاً در دسترس نیست."
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("🗑 پاک‌سازی", "adm:cache:clear")], [("⬅️ ابزارها", "adm:tools")],
        ]))

    def show_stats(self, chat_id: int, message_id: int | None = None) -> None:
        try:
            bot = self.services.stats().get("bot")
            text = (
                "📊 <b>آمار ربات</b>\n\n"
                f"پرسش‌ها: {getattr(bot, 'questions', '—')}\n"
                f"پاسخ موفق: {getattr(bot, 'successes', '—')}\n"
                f"خطاها: {getattr(bot, 'failures', '—')}\n"
                f"میانگین زمان پاسخ: {float(getattr(bot, 'average_latency_ms', 0)) / 1000:.1f} ثانیه"
            )
        except Exception:
            text = "📊 <b>آمار ربات</b>\n\n🟡 آمار فعلاً در دسترس نیست."
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("🔄 تازه‌سازی", "adm:stats")], [("⬅️ ابزارها", "adm:tools")],
        ]))

    def check_for_updates(self, *, force: bool = False) -> bool:
        mode = self._update_mode()
        if mode == UPDATE_MODE_OFF and not force:
            return False
        remote = self._remote()
        if remote is None or not getattr(remote, "update_available", False):
            return False
        latest = str(getattr(remote, "latest_sha", "") or "")
        if not latest:
            return False
        ci_status = str(getattr(remote, "ci_status", "unknown") or "unknown")
        status = self._status()
        current = getattr(remote, "current_sha", None)
        # Automatic installation is allowed only for an established active release,
        # a green exact SHA, and an idle updater. First/bootstrap deployment remains manual.
        if mode == UPDATE_MODE_AUTO and current and ci_status == "success" and not status.active:
            try:
                request_id, verified = self.services.request_software_update(source="auto")
            except TypeError:
                request_id = self.services.request_software_update()
                verified = remote
            except RuntimeError:
                return False
            result = self.api.send_message(
                self.owner_id,
                self._queued_auto_text(verified),
                reply_markup=inline_keyboard([[("📋 مشاهده وضعیت", "adm:update")]]),
            )
            message_id = _message_id(result)
            if message_id:
                self._bind(request_id, self.owner_id, message_id, getattr(verified, "latest_sha", None))
            return True

        kind = f"available:{ci_status}:{mode}"
        if not self._claim_notification(latest, kind):
            return False
        ci_icon, ci_label = _CI_META.get(ci_status, _CI_META["unknown"])
        lines = [
            "🆕 <b>نسخه جدید DrJavanBot</b>", "",
            f"نسخه فعلی: <code>{_short(current)}</code>",
            f"نسخه جدید: <code>{_short(latest)}</code>",
            f"{ci_icon} {ci_label}",
        ]
        rows: list[list[tuple[str, str]]] = []
        if ci_status == "success" and mode != UPDATE_MODE_OFF:
            rows.append([("⬆️ نصب", "adm:update:install"), ("📋 جزئیات", "adm:update")])
        else:
            rows.append([("📋 جزئیات", "adm:update")])
        rows.append([("بعداً", "adm:home")])
        self.api.send_message(self.owner_id, "\n".join(lines), reply_markup=inline_keyboard(rows))
        return True

    def sync_update_progress(self) -> bool:
        try:
            binding = self.services.update_progress_binding()
        except Exception:
            return False
        if binding is None:
            return False
        status = self._status()
        if status.request_id and status.request_id != binding.request_id:
            return False
        try:
            self.api.edit_message_text(
                binding.chat_id,
                binding.message_id,
                self._progress_text(status),
                reply_markup=self._progress_keyboard(status),
            )
        except TelegramAPIError as exc:
            if "not modified" in str(exc).casefold():
                return True
            return False
        except Exception:
            return False
        if status.terminal:
            try:
                self.services.clear_update_progress_binding(status.request_id)
            except Exception:
                pass
        return True

    def notify_update_if_available(self, *, force: bool = False) -> bool:
        return self.check_for_updates(force=force)

    def _install(self, chat_id: int, message_id: int) -> None:
        try:
            try:
                request_id, info = self.services.request_software_update(source="manual")
            except TypeError:
                request_id = self.services.request_software_update()
                info = self._cached_remote()
                # Compatibility with pre-v2 service fakes/callers: acknowledge the
                # fixed request rather than pretending structured progress exists.
                self._send_or_edit(
                    chat_id, message_id,
                    f"✅ درخواست به‌روزرسانی ثبت شد.\nشناسه: <code>{html_escape(str(request_id))}</code>",
                    inline_keyboard([[("📋 وضعیت", "adm:update")]]),
                )
                return
            target = getattr(info, "latest_sha", None) if info is not None else None
            self._bind(request_id, chat_id, message_id, target)
            self._send_or_edit(chat_id, message_id, self._progress_text(self._status()), self._progress_keyboard(self._status()))
        except RuntimeError as exc:
            reason = str(exc)
            if "already up to date" in reason:
                text = "🟢 ربات همین حالا به‌روز است."
            elif "CI" in reason or "green" in reason:
                text = "🟡 نصب شروع نشد؛ تست‌های GitHub هنوز موفق نشده‌اند."
            elif "pending" in reason:
                text = "🟡 یک به‌روزرسانی دیگر در حال اجراست."
            else:
                text = "🟡 نصب شروع نشد؛ وضعیت GitHub یا سامانه به‌روزرسانی را دوباره بررسی کنید."
            self._send_or_edit(chat_id, message_id, text, inline_keyboard([
                [("🔄 تازه‌سازی", "adm:update")], [("⬅️ مرکز مدیریت", "adm:home")],
            ]))

    def _rollback(self, chat_id: int, message_id: int) -> None:
        try:
            request_id = self.services.request_rollback()
            self._bind(request_id, chat_id, message_id, self._previous_sha())
            status = self._status()
            self._send_or_edit(chat_id, message_id, self._progress_text(status), self._progress_keyboard(status))
        except RuntimeError:
            self._send_or_edit(
                chat_id, message_id,
                "🟡 بازگشت شروع نشد؛ عملیات دیگری در حال اجراست یا نسخه قبلی در دسترس نیست.",
                inline_keyboard([[("⬅️ به‌روزرسانی", "adm:update")]]),
            )

    def _confirm_auto_mode(self, chat_id: int, message_id: int) -> None:
        text = (
            "⚠️ <b>فعال‌سازی نصب خودکار</b>\n\n"
            "فقط نسخه‌ای نصب می‌شود که همان شناسه نسخه (SHA) در GitHub تست‌های موفق داشته باشد. "
            "اگر بررسی‌های محیط اجرا یا سلامت ناموفق باشند، نسخه فعال حفظ می‌شود یا سامانه بازگشت ایمن را انجام می‌دهد.\n\n"
            "نصب خودکار فعال شود؟"
        )
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("✅ فعال شود", "adm:update:mode:auto:yes")], [("انصراف", "adm:update:settings")],
        ]))

    def _confirm_rollback(self, chat_id: int, message_id: int) -> None:
        current = self._current_sha()
        previous = self._previous_sha()
        if not previous:
            self._send_or_edit(chat_id, message_id, "🟡 نسخه سالم قبلی برای بازگشت پیدا نشد.", inline_keyboard([[("⬅️ به‌روزرسانی", "adm:update")]]))
            return
        text = (
            "↩️ <b>بازگشت به نسخه قبلی</b>\n\n"
            f"نسخه فعلی: <code>{_short(current)}</code>\n"
            f"نسخه مقصد: <code>{_short(previous)}</code>\n\n"
            "این عملیات فقط DrJavanBot را تغییر می‌دهد. ادامه دهیم؟"
        )
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([
            [("✅ بازگشت", "adm:update:rollback:yes")], [("انصراف", "adm:update")],
        ]))

    def _confirm_remove_key(self, chat_id: int, message_id: int) -> None:
        self._send_or_edit(chat_id, message_id, "🗑 <b>حذف کلید API</b>\n\nکلید فعلی حذف شود؟", inline_keyboard([
            [("✅ حذف", "adm:ai:remove:yes")], [("انصراف", "adm:ai")],
        ]))

    def _remove_key(self, chat_id: int, message_id: int) -> None:
        self.services.remove_api_key()
        self.show_ai(chat_id, message_id, note="✅ کلید API حذف شد.")

    def _confirm_reindex(self, chat_id: int, message_id: int) -> None:
        self._send_or_edit(
            chat_id, message_id,
            "🔄 <b>بازسازی ایندکس</b>\n\nاین عملیات پردازش سنگین‌تری دارد و فقط در صورت نیاز باید اجرا شود. ادامه دهیم؟",
            inline_keyboard([[("✅ بازسازی", "adm:archive:reindex:yes")], [("انصراف", "adm:archive")]]),
        )

    def _test_ai(self, chat_id: int, message_id: int) -> None:
        try:
            note = "✅ اتصال هوش مصنوعی سالم است." if self.services.test_ai() else "🔴 کلید تنظیم نشده یا معتبر نیست."
        except Exception:
            note = "🟡 سرویس هوش مصنوعی فعلاً پاسخ نداد."
        self.show_ai(chat_id, message_id, note=note)

    def _show_models(self, chat_id: int, message_id: int) -> None:
        current = str(self.services.model())
        rows = []
        for model in ALLOWED_MODELS:
            title = "سریع" if model.endswith("flash") else "دقیق"
            rows.append([(("✅ " if model == current else "") + title, f"adm:ai:model:{model}")])
        rows.append([("⬅️ هوش مصنوعی", "adm:ai")])
        self._send_or_edit(
            chat_id, message_id,
            "🤖 <b>انتخاب مدل</b>\n\nمدل فعلی: <code>%s</code>" % html_escape(current),
            inline_keyboard(rows),
        )

    def _set_model(self, chat_id: int, message_id: int, model: str) -> None:
        if model not in ALLOWED_MODELS:
            return
        self.services.set_model(model)
        self.show_ai(chat_id, message_id, note="✅ مدل تغییر کرد.")

    def _set_access_mode(self, mode: str) -> None:
        try:
            self.state.set_access_mode(mode)
        except ValueError:
            pass

    def _set_rate(self, raw: str) -> None:
        try:
            self.state.set_rate_limit_per_minute(int(raw))
        except (TypeError, ValueError):
            pass

    def _clear_cache(self, chat_id: int, message_id: int) -> None:
        try:
            text = f"✅ {int(self.services.clear_cache())} ورودی حافظه موقت پاک شد."
        except Exception:
            text = "🟡 پاک‌سازی حافظه موقت انجام نشد."
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([[("⬅️ ابزارها", "adm:tools")]]))

    def _set_update_mode(self, mode: str) -> None:
        setter = getattr(self.services, "set_update_mode", None)
        if callable(setter):
            setter(mode)
            return
        setter = getattr(self.state, "set_update_mode", None)
        if callable(setter):
            setter(mode)

    def _update_mode(self) -> str:
        getter = getattr(self.services, "update_mode", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                pass
        getter = getattr(self.state, "update_mode", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                pass
        return UPDATE_MODE_NOTIFY

    def _remote(self):
        try:
            return self.services.remote_update_info()
        except Exception:
            return None

    def _cached_remote(self):
        getter = getattr(self.services, "cached_remote_update_info", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _status(self) -> UpdateStatus:
        try:
            return self.services.update_status()
        except Exception:
            return UpdateStatus(state="unknown")

    def _safe_health(self) -> dict[str, Any]:
        try:
            value = self.services.health()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _history(self, *, limit: int):
        getter = getattr(self.services, "update_history", None)
        if not callable(getter):
            return ()
        try:
            return tuple(getter(limit=limit))
        except Exception:
            return ()

    def _current_sha(self) -> str | None:
        getter = getattr(self.services, "current_release_sha", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                pass
        return getattr(self._status(), "current_sha", None)

    def _previous_sha(self) -> str | None:
        getter = getattr(self.services, "previous_release_sha", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _claim_notification(self, sha: str, kind: str) -> bool:
        claim = getattr(self.services, "claim_update_notification", None)
        if not callable(claim):
            return False
        try:
            return bool(claim(sha, kind))
        except TypeError:
            try:
                return bool(claim(sha))
            except Exception:
                return False
        except Exception:
            return False

    def _bind(self, request_id: str, chat_id: int, message_id: int, target_sha: str | None) -> None:
        binder = getattr(self.services, "bind_update_progress_message", None)
        if callable(binder):
            try:
                binder(request_id, chat_id, message_id, target_sha=target_sha)
            except Exception:
                pass

    def _progress_text(self, status: UpdateStatus) -> str:
        icon, label = _STATE_META.get(str(status.state), _STATE_META["unknown"])
        current = int(status.progress_current or 0)
        total = int(status.progress_total or 0)
        lines = ["🔄 <b>به‌روزرسانی DrJavanBot</b>", ""]
        if status.target_sha:
            lines.append(f"نسخه: <code>{_short(status.target_sha)}</code>")
        lines.append(f"وضعیت: {icon} {html_escape(label)}")
        if total:
            lines += [f"مرحله {min(current, total)} از {total}", ""]
            for index, stage_title in enumerate(_PROGRESS_STAGES, start=1):
                if status.state in {"success", "rolled_back", "up_to_date"} and index <= max(current, total):
                    mark = "✅"
                elif index < current:
                    mark = "✅"
                elif index == current and status.state not in {"failed", "success", "rolled_back", "up_to_date"}:
                    mark = "🔵"
                else:
                    mark = "⚪"
                lines.append(f"{mark} {stage_title}")
        activity = status.message or status.stage_label
        if activity:
            lines += ["", f"آخرین فعالیت: {html_escape(str(activity))}"]
        # Running details are curated progress text. Failure diagnostics stay
        # behind the explicit Details action instead of being dumped into chat.
        if status.detail and status.state != "failed" and status.detail != activity:
            lines.append(html_escape(str(status.detail)))
        if status.change_class:
            lines.append(f"نوع انتشار: {_change_class_label(status.change_class)}")
        if status.error_id:
            lines.append(f"شناسه خطا: <code>{html_escape(status.error_id)}</code>")
        if status.duration_seconds is not None and status.terminal:
            lines.append(f"زمان عملیات: {float(status.duration_seconds):.1f} ثانیه")
        return "\n".join(lines)

    def _progress_keyboard(self, status: UpdateStatus) -> dict:
        if status.state == "failed":
            return inline_keyboard([
                [("📋 جزئیات خطا", "adm:update:error"), ("🔄 تلاش دوباره", "adm:update:install")],
                [("🕘 سابقه نسخه‌ها", "adm:update:history")], [("⬅️ به‌روزرسانی", "adm:update")],
            ])
        if status.terminal:
            return inline_keyboard([[("📋 وضعیت نسخه", "adm:update")], [("⬅️ مرکز مدیریت", "adm:home")]])
        return inline_keyboard([[("🔄 تازه‌سازی", "adm:update")], [("⬅️ مرکز مدیریت", "adm:home")]])

    def _queued_auto_text(self, info) -> str:
        return (
            "🔄 <b>به‌روزرسانی خودکار آغاز شد</b>\n\n"
            f"نسخه مقصد: <code>{_short(getattr(info, 'latest_sha', None))}</code>\n"
            "🟢 تست‌های GitHub موفق\n\n"
            "وضعیت همین پیام به‌صورت زنده به‌روزرسانی می‌شود."
        )

    def _send_or_edit(self, chat_id: int, message_id: int | None, text: str, keyboard: dict | None) -> None:
        if message_id is None:
            self.api.send_message(chat_id, text, reply_markup=keyboard)
            return
        try:
            self.api.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
        except TelegramAPIError as exc:
            if "not modified" not in str(exc).casefold():
                raise


def _index_ok(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if "ok" in value:
        return bool(value.get("ok"))
    return bool(value.get("healthy", False))


def _short(value) -> str:
    text = str(value or "").strip()
    return html_escape(text[:12]) if text else "—"


def _state_icon(state: str) -> str:
    return _STATE_META.get(str(state), _STATE_META["unknown"])[0]


def _state_label(state: str) -> str:
    return _STATE_META.get(str(state), _STATE_META["unknown"])[1]


def _message_id(result) -> int | None:
    if not isinstance(result, dict):
        return None
    try:
        value = int(result.get("message_id"))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _event_time(item: dict) -> str:
    raw = item.get("completed_at") or item.get("updated_at") or item.get("started_at")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ""


def _history_result(item: dict) -> str:
    result = str(item.get("result") or item.get("state") or "").casefold()
    action = str(item.get("action") or "").casefold()
    if action == "rollback" or result == "rolled_back":
        return "↩️ بازگشت"
    if result in {"success", "healthy", "up_to_date"}:
        return "✅ موفق"
    if result in {"failed", "failure"}:
        return "🔴 ناموفق"
    return "⚪ ثبت‌شده"


def _change_class_label(value: str) -> str:
    return {
        "code_only": "فقط کد",
        "dependency": "وابستگی‌ها",
        "index": "کد ایندکس",
        "archive": "آرشیو",
        "mixed": "ترکیبی",
        "rollback": "بازگشت نسخه",
    }.get(str(value), html_escape(str(value)))


__all__ = ["AdminControlCenter", "V"]
