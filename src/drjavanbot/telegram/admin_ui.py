from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from .api import TelegramAPIError
from .config import ALLOWED_MODELS
from .rendering import html_escape, inline_keyboard
from .update_control import (
    ACTIVE_UPDATE_STATES,
    UPDATE_MODE_AUTO,
    UPDATE_MODE_NOTIFY,
    UPDATE_MODE_OFF,
)

_LOG = logging.getLogger(__name__)


class V:
    """Centralized Persian vocabulary for the owner UI."""

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
    ("بررسی نسخه", "checking"),
    ("بررسی تست‌های GitHub", "ci"),
    ("آماده‌سازی نسخه", "preparing"),
    ("اعتبارسنجی production", "validating"),
    ("فعال‌سازی", "switching"),
    ("بررسی سلامت و تلگرام", "verifying"),
    ("پایان", "done"),
)


class AdminControlCenter:
    """Product-grade owner UI with no business-side shell access."""

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

        # Backward compatibility for buttons from pre-v2 messages.
        data = {
            "settings": "adm:home",
            "software_update": "adm:update",
            "software_update_confirm": "adm:update:install",
            "software_rollback": "adm:update:rollback",
            "software_rollback_confirm": "adm:update:rollback:yes",
            "recent_errors": "adm:errors",
        }.get(data, data)

        if data == "adm:home": self.show_home(chat_id, message_id); return
        if data == "adm:update": self.show_update(chat_id, message_id, refresh=True); return
        if data == "adm:update:install": self._install(chat_id, message_id, auto=False); return
        if data == "adm:update:settings": self.show_update_settings(chat_id, message_id); return
        if data == "adm:update:mode:notify": self._set_update_mode(UPDATE_MODE_NOTIFY); self.show_update_settings(chat_id, message_id); return
        if data == "adm:update:mode:off": self._set_update_mode(UPDATE_MODE_OFF); self.show_update_settings(chat_id, message_id); return
        if data == "adm:update:mode:auto": self._confirm_auto_mode(chat_id, message_id); return
        if data == "adm:update:mode:auto:yes": self._set_update_mode(UPDATE_MODE_AUTO); self.show_update_settings(chat_id, message_id); return
        if data == "adm:update:history": self.show_update_history(chat_id, message_id); return
        if data == "adm:update:rollback": self._confirm_rollback(chat_id, message_id); return
        if data == "adm:update:rollback:yes": self._rollback(chat_id, message_id); return
        if data == "adm:health": self.show_health(chat_id, message_id); return
        if data == "adm:ai": self.show_ai(chat_id, message_id); return
        if data == "adm:ai:key": self.state.begin_flow(user_id, "await_avalai_key", self.config.key_entry_timeout_seconds); self.api.send_message(chat_id, "🔑 کلید API را همین‌جا بفرستید. پیام حاوی کلید پس از دریافت حذف می‌شود.\n/cancel برای لغو."); return
        if data == "adm:ai:test": self._test_ai(chat_id, message_id); return
        if data == "adm:ai:remove": self._confirm_remove_key(chat_id, message_id); return
        if data == "adm:ai:remove:yes": self.services.remove_api_key(); self.show_ai(chat_id, message_id, note="✅ کلید API حذف شد."); return
        if data == "adm:ai:models": self._show_models(chat_id, message_id); return
        if data.startswith("adm:ai:model:"): self._set_model(chat_id, message_id, data.split(":", 3)[-1]); return
        if data == "adm:archive": self.show_archive(chat_id, message_id); return
        if data == "adm:archive:reindex": self._confirm_reindex(chat_id, message_id); return
        if data == "adm:archive:reindex:yes": self.app._start_reindex(chat_id); return
        if data == "adm:access": self.show_access(chat_id, message_id); return
        if data.startswith("adm:access:mode:"): self._set_access_mode(data.rsplit(":", 1)[-1]); self.show_access(chat_id, message_id); return
        if data.startswith("adm:access:rate:"): self._set_rate(data.rsplit(":", 1)[-1]); self.show_access(chat_id, message_id); return
        if data == "adm:tools": self.show_tools(chat_id, message_id); return
        if data == "adm:errors": self.show_errors(chat_id, message_id); return
        if data == "adm:cache": self.show_cache(chat_id, message_id); return
        if data == "adm:cache:clear": self._clear_cache(chat_id, message_id); return
        if data == "adm:stats": self.show_stats(chat_id, message_id); return

    def show_home(self, chat_id: int, message_id: int | None = None) -> None:
        health = self._safe_health()
        remote = self._cached_remote()
        status = self._status()
        index_ok = bool((health.get("index") or {}).get("ok", (health.get("index") or {}).get("messages", 0) > 0))
        ai_ok = bool(health.get("ai_configured")) and not bool(health.get("provider_auth_failed"))
        current = status.current_sha or self._current_sha()
        if status.active:
            update_line = "🔵 به‌روزرسانی در حال اجرا"
        elif remote is not None and getattr(remote, "update_available", False):
            update_line = "🟡 نسخه جدید موجود است"
        else:
            update_line = "🟢 به‌روز" if remote is not None else "⚪ در انتظار بررسی"
        overall = "🟢 سالم" if index_ok and ai_ok and status.state != "failed" else "🟡 نیازمند توجه"
        text = (
            "🦷 <b>مرکز مدیریت DrJavanBot</b>\n"
            f"وضعیت کلی: {overall}\n\n"
            f"نسخه: <code>{_short(current)}</code>\n"
            f"ایندکس: {'🟢 آماده' if index_ok else '🟡 نیازمند بررسی'}\n"
            f"هوش مصنوعی: {'🟢 متصل' if ai_ok else '🟡 نیازمند بررسی'}\n"
            f"به‌روزرسانی: {update_line}"
        )
        kb = inline_keyboard([
            [("🔄 به‌روزرسانی", "adm:update"), ("❤️ سلامت سیستم", "adm:health")],
            [("🤖 هوش مصنوعی", "adm:ai"), ("📚 آرشیو و ایندکس", "adm:archive")],
            [("👥 دسترسی کاربران", "adm:access"), ("🧰 ابزارها", "adm:tools")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_update(self, chat_id: int, message_id: int | None = None, *, refresh: bool = False) -> None:
        status = self._status()
        if status.active:
            self._send_or_edit(chat_id, message_id, self._progress_text(status), self._progress_keyboard(status))
            return
        remote = self._remote() if refresh or self._cached_remote() is None else self._cached_remote()
        current = status.current_sha or self._current_sha()
        mode = self._update_mode()
        lines = [
            "🔄 <b>به‌روزرسانی نرم‌افزار</b>",
            "",
            f"نسخه نصب‌شده: <code>{_short(current)}</code>",
        ]
        installable = False
        if remote is None:
            lines += ["آخرین نسخه: —", "", "🟡 ارتباط با GitHub فعلاً قابل بررسی نیست."]
        else:
            lines.append(f"آخرین نسخه: <code>{_short(getattr(remote, 'latest_sha', None))}</code>")
            ci_status = str(getattr(remote, "ci_status", "unknown") or "unknown")
            ci_icon, ci_label = _CI_META.get(ci_status, _CI_META["unknown"])
            if not getattr(remote, "update_available", False):
                lines += ["", "🟢 ربات به‌روز است.", f"{ci_icon} {ci_label}"]
            else:
                lines += ["", "🟡 نسخه جدید موجود است.", f"{ci_icon} {ci_label}"]
                installable = ci_status == "success"
        lines += ["", f"حالت به‌روزرسانی: <b>{html_escape(_MODE_LABELS.get(mode, mode))}</b>"]
        last = self._history(limit=1)
        if last:
            stamp = _event_time(last[0])
            result = _history_result(last[0])
            lines.append(f"آخرین عملیات: {result}{(' — ' + stamp) if stamp else ''}")
        rows = []
        if installable:
            rows.append([("⬆️ نصب نسخه جدید", "adm:update:install")])
        rows += [
            [("⚙️ تنظیمات", "adm:update:settings"), ("🕘 سابقه نسخه‌ها", "adm:update:history")],
            [("↩️ نسخه قبلی", "adm:update:rollback"), ("🔄 تازه‌سازی", "adm:update")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ]
        self._send_or_edit(chat_id, message_id, "\n".join(lines), inline_keyboard(rows))

    def show_update_settings(self, chat_id: int, message_id: int | None = None) -> None:
        mode = self._update_mode()
        text = (
            "⚙️ <b>تنظیمات به‌روزرسانی</b>\n\n"
            f"حالت فعلی: <b>{html_escape(_MODE_LABELS.get(mode, mode))}</b>\n\n"
            "• فقط اطلاع‌رسانی: نسخه جدید را اعلام می‌کند و نصب با شماست.\n"
            "• نصب خودکار: فقط exact SHA با تست‌های سبز GitHub نصب می‌شود.\n"
            "• غیرفعال: بررسی خودکار نسخه جدید متوقف می‌شود."
        )
        def label(value: str, text: str) -> str:
            return ("✅ " if mode == value else "") + text
        kb = inline_keyboard([
            [(label(UPDATE_MODE_NOTIFY, "فقط اطلاع‌رسانی"), "adm:update:mode:notify")],
            [(label(UPDATE_MODE_AUTO, "نصب خودکار تأییدشده"), "adm:update:mode:auto")],
            [(label(UPDATE_MODE_OFF, "غیرفعال"), "adm:update:mode:off")],
            [("⬅️ به‌روزرسانی", "adm:update")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_update_history(self, chat_id: int, message_id: int | None = None) -> None:
        items = self._history(limit=10)
        lines = ["🕘 <b>سابقه نسخه‌ها</b>"]
        if not items:
            lines += ["", "هنوز سابقه‌ای ثبت نشده است."]
        else:
            for item in items:
                sha = _short(item.get("target_sha"))
                result = _history_result(item)
                stamp = _event_time(item)
                duration = item.get("duration_seconds")
                suffix = f" — {int(float(duration))}ث" if duration is not None else ""
                lines.append(f"{result} — <code>{sha}</code>{(' — ' + stamp) if stamp else ''}{suffix}")
        kb = inline_keyboard([[("🔄 تازه‌سازی", "adm:update:history")], [("⬅️ به‌روزرسانی", "adm:update")]])
        self._send_or_edit(chat_id, message_id, "\n".join(lines), kb)

    def show_health(self, chat_id: int, message_id: int | None = None) -> None:
        health = self._safe_health()
        index = health.get("index") or {}
        updater = health.get("updater") or {}
        storage = health.get("storage") or {}
        ai_ok = bool(health.get("ai_configured")) and not bool(health.get("provider_auth_failed"))
        index_ok = bool(index.get("ok", index.get("messages", 0) > 0))
        updater_state = str(updater.get("state") or "idle")
        disk_percent = storage.get("used_percent")
        disk_line = f"{float(disk_percent):.1f}% استفاده" if isinstance(disk_percent, (int, float)) else "نامشخص"
        text = (
            "❤️ <b>سلامت سیستم</b>\n\n"
            "🟢 سرویس ربات: فعال\n"
            f"{'🟢' if ai_ok else '🟡'} هوش مصنوعی: {'متصل' if ai_ok else 'نیازمند بررسی'}\n"
            f"{'🟢' if index_ok else '🟡'} آرشیو و ایندکس: {'آماده' if index_ok else 'نیازمند بررسی'}\n"
            f"{_state_icon(updater_state)} سامانه به‌روزرسانی: {_state_label(updater_state)}\n"
            f"💾 فضای ذخیره‌سازی: {html_escape(disk_line)}"
        )
        kb = inline_keyboard([[("🔄 تازه‌سازی", "adm:health")], [("🧯 خطاهای اخیر", "adm:errors")], [("⬅️ مرکز مدیریت", "adm:home")]])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_ai(self, chat_id: int, message_id: int | None = None, *, note: str | None = None) -> None:
        configured = bool(self.services.ai_configured())
        model = html_escape(str(self.services.model()))
        lines = [
            "🤖 <b>هوش مصنوعی</b>", "",
            f"اتصال: {'🟢 تنظیم شده' if configured else '🟡 تنظیم نشده'}",
            f"مدل فعلی: <code>{model}</code>",
        ]
        if note: lines += ["", note]
        kb = inline_keyboard([
            [("🔑 تنظیم کلید API", "adm:ai:key"), ("🧪 تست اتصال", "adm:ai:test")],
            [("🤖 انتخاب مدل", "adm:ai:models"), ("🗑 حذف کلید", "adm:ai:remove")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ])
        self._send_or_edit(chat_id, message_id, "\n".join(lines), kb)

    def show_archive(self, chat_id: int, message_id: int | None = None) -> None:
        try:
            stats = self.services.stats()
        except Exception:
            stats = {}
        index = stats.get("index") or {}
        messages = index.get("messages", index.get("message_count", "—"))
        last_reindex = stats.get("last_reindex_at") or "—"
        text = (
            "📚 <b>آرشیو و ایندکس</b>\n\n"
            f"پیام‌های ایندکس‌شده: <b>{html_escape(str(messages))}</b>\n"
            f"آخرین بازسازی: {html_escape(str(last_reindex))}\n"
            f"وضعیت: {'🟢 آماده' if messages not in (0, '0', '—') else '🟡 نیازمند بررسی'}"
        )
        kb = inline_keyboard([
            [("🔄 بازسازی ایندکس", "adm:archive:reindex")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_access(self, chat_id: int, message_id: int | None = None) -> None:
        mode = str(self.state.access_mode())
        rate = int(self.state.rate_limit_per_minute())
        mode_label = {"owner_only": "فقط مالک", "allowlist": "فهرست مجاز", "public": "عمومی"}.get(mode, mode)
        text = (
            "👥 <b>دسترسی کاربران</b>\n\n"
            f"حالت: <b>{html_escape(mode_label)}</b>\n"
            f"محدودیت درخواست: <b>{rate}</b> در دقیقه"
        )
        kb = inline_keyboard([
            [("فقط مالک", "adm:access:mode:owner_only"), ("فهرست مجاز", "adm:access:mode:allowlist")],
            [("عمومی", "adm:access:mode:public")],
            [("۳ در دقیقه", "adm:access:rate:3"), ("۶ در دقیقه", "adm:access:rate:6"), ("۱۲ در دقیقه", "adm:access:rate:12")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_tools(self, chat_id: int, message_id: int | None = None) -> None:
        text = "🧰 <b>ابزارها و عیب‌یابی</b>\n\nابزارهای کم‌استفاده در این بخش نگه داشته شده‌اند تا صفحه اصلی شلوغ نشود."
        kb = inline_keyboard([
            [("📊 آمار", "adm:stats"), ("🧹 حافظه موقت", "adm:cache")],
            [("🧯 خطاهای اخیر", "adm:errors")],
            [("⬅️ مرکز مدیریت", "adm:home")],
        ])
        self._send_or_edit(chat_id, message_id, text, kb)

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
            lines += ["", "برای بررسی فقط شناسه خطا و نوع خطا کافی است؛ متن سؤال و secrets اینجا نمایش داده نمی‌شوند."]
        kb = inline_keyboard([[("🔄 تازه‌سازی", "adm:errors")], [("⬅️ ابزارها", "adm:tools")]])
        self._send_or_edit(chat_id, message_id, "\n".join(lines), kb)

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
        kb = inline_keyboard([[("🗑 پاک‌سازی", "adm:cache:clear")], [("⬅️ ابزارها", "adm:tools")]])
        self._send_or_edit(chat_id, message_id, text, kb)

    def show_stats(self, chat_id: int, message_id: int | None = None) -> None:
        try:
            stats = self.services.stats()
            bot = stats.get("bot")
            text = (
                "📊 <b>آمار ربات</b>\n\n"
                f"پرسش‌ها: {getattr(bot, 'questions', '—')}\n"
                f"پاسخ موفق: {getattr(bot, 'successes', '—')}\n"
                f"خطاها: {getattr(bot, 'failures', '—')}\n"
                f"میانگین زمان پاسخ: {float(getattr(bot, 'average_latency_ms', 0)) / 1000:.1f} ثانیه"
            )
        except Exception:
            text = "📊 <b>آمار ربات</b>\n\n🟡 آمار فعلاً در دسترس نیست."
        kb = inline_keyboard([[("🔄 تازه‌سازی", "adm:stats")], [("⬅️ ابزارها", "adm:tools")]])
        self._send_or_edit(chat_id, message_id, text, kb)

    def check_for_updates(self, *, force: bool = False) -> bool:
        mode = self._update_mode()
        if mode == UPDATE_MODE_OFF and not force:
            return False
        remote = self._remote()
        if remote is None or not getattr(remote, "update_available", False):
            return False
        ci_status = str(getattr(remote, "ci_status", "unknown") or "unknown")
        latest = str(getattr(remote, "latest_sha", "") or "")
        if not latest:
            return False
        status = self._status()
        if mode == UPDATE_MODE_AUTO and ci_status == "success" and not status.active:
            try:
                request_id, verified = self.services.request_software_update(source="auto")
            except TypeError:
                request_id = self.services.request_software_update()
                verified = remote
            except RuntimeError:
                return False
            text = self._queued_auto_text(verified)
            result = self.api.send_message(self.owner_id, text, reply_markup=inline_keyboard([[("📋 مشاهده وضعیت", "adm:update")]]))
            mid = _message_id(result)
            if mid:
                self._bind(request_id, self.owner_id, mid, getattr(verified, "latest_sha", None))
            return True
        kind = f"available:{ci_status}:{mode}"
        if not self._claim_notification(latest, kind):
            return False
        current = getattr(remote, "current_sha", None)
        ci_icon, ci_label = _CI_META.get(ci_status, _CI_META["unknown"])
        lines = [
            "🆕 <b>نسخه جدید DrJavanBot</b>", "",
            f"نسخه فعلی: <code>{_short(current)}</code>",
            f"نسخه جدید: <code>{_short(latest)}</code>",
            f"{ci_icon} {ci_label}",
        ]
        rows = []
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
        except TelegramAPIError:
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

    def _install(self, chat_id: int, message_id: int, *, auto: bool) -> None:
        try:
            try:
                request_id, info = self.services.request_software_update(source="auto" if auto else "manual")
            except TypeError:
                request_id = self.services.request_software_update()
                info = self._cached_remote()
            target = getattr(info, "latest_sha", None) if info is not None else None
            self._bind(request_id, chat_id, message_id, target)
            status = self._status()
            self._send_or_edit(chat_id, message_id, self._progress_text(status), self._progress_keyboard(status))
        except RuntimeError as exc:
            reason = str(exc)
            if "already up to date" in reason:
                message = "🟢 ربات همین حالا به‌روز است."
            elif "CI" in reason or "green" in reason:
                message = "🟡 نصب شروع نشد؛ تست‌های GitHub هنوز سبز نیستند."
            elif "pending" in reason:
                message = "🟡 یک به‌روزرسانی دیگر در حال اجراست."
            else:
                message = "🟡 نصب شروع نشد؛ وضعیت GitHub یا updater را دوباره بررسی کنید."
            self._send_or_edit(chat_id, message_id, message, inline_keyboard([[("🔄 تازه‌سازی", "adm:update")], [("⬅️ مرکز مدیریت", "adm:home")]]))

    def _rollback(self, chat_id: int, message_id: int) -> None:
        try:
            request_id = self.services.request_rollback()
            self._bind(request_id, chat_id, message_id, self._previous_sha())
            self._send_or_edit(chat_id, message_id, self._progress_text(self._status()), self._progress_keyboard(self._status()))
        except RuntimeError:
            self._send_or_edit(chat_id, message_id, "🟡 بازگشت شروع نشد؛ عملیات دیگری در حال اجراست یا نسخه قبلی در دسترس نیست.", inline_keyboard([[("⬅️ به‌روزرسانی", "adm:update")]]))

    def _confirm_auto_mode(self, chat_id: int, message_id: int) -> None:
        text = (
            "⚠️ <b>فعال‌سازی نصب خودکار</b>\n\n"
            "فقط نسخه‌ای نصب می‌شود که exact SHA آن در GitHub تست‌های موفق داشته باشد. "
            "در صورت خطای preflight یا سلامت، نسخه فعال دست‌نخورده می‌ماند یا rollback می‌شود.\n\n"
            "نصب خودکار فعال شود؟"
        )
        kb = inline_keyboard([[("✅ فعال شود", "adm:update:mode:auto:yes")], [("انصراف", "adm:update:settings")]])
        self._send_or_edit(chat_id, message_id, text, kb)

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
        kb = inline_keyboard([[("✅ بازگشت", "adm:update:rollback:yes")], [("انصراف", "adm:update")]])
        self._send_or_edit(chat_id, message_id, text, kb)

    def _confirm_remove_key(self, chat_id: int, message_id: int) -> None:
        self._send_or_edit(chat_id, message_id, "🗑 <b>حذف کلید API</b>\n\nکلید فعلی حذف شود؟", inline_keyboard([[("✅ حذف", "adm:ai:remove:yes")], [("انصراف", "adm:ai")]]))

    def _confirm_reindex(self, chat_id: int, message_id: int) -> None:
        self._send_or_edit(chat_id, message_id, "🔄 <b>بازسازی ایندکس</b>\n\nاین عملیات پردازش سنگین‌تری دارد و فقط در صورت نیاز باید اجرا شود. ادامه دهیم؟", inline_keyboard([[("✅ بازسازی", "adm:archive:reindex:yes")], [("انصراف", "adm:archive")]]))

    def _test_ai(self, chat_id: int, message_id: int) -> None:
        try:
            ok = bool(self.services.test_ai())
            note = "✅ اتصال هوش مصنوعی سالم است." if ok else "🔴 کلید تنظیم نشده یا معتبر نیست."
        except Exception:
            note = "🟡 سرویس هوش مصنوعی فعلاً پاسخ نداد."
        self.show_ai(chat_id, message_id, note=note)

    def _show_models(self, chat_id: int, message_id: int) -> None:
        current = str(self.services.model())
        rows = []
        for model in ALLOWED_MODELS:
            label = ("✅ " if model == current else "") + ("سریع" if model.endswith("flash") else "دقیق")
            rows.append([(label, f"adm:ai:model:{model}")])
        rows.append([("⬅️ هوش مصنوعی", "adm:ai")])
        self._send_or_edit(chat_id, message_id, "🤖 <b>انتخاب مدل</b>\n\nمدل فعلی: <code>%s</code>" % html_escape(current), inline_keyboard(rows))

    def _set_model(self, chat_id: int, message_id: int, model: str) -> None:
        if model not in ALLOWED_MODELS:
            return
        self.services.set_model(model)
        self.show_ai(chat_id, message_id, note="✅ مدل تغییر کرد.")

    def _set_access_mode(self, mode: str) -> None:
        try: self.state.set_access_mode(mode)
        except ValueError: pass

    def _set_rate(self, raw: str) -> None:
        try: self.state.set_rate_limit_per_minute(int(raw))
        except (TypeError, ValueError): pass

    def _clear_cache(self, chat_id: int, message_id: int) -> None:
        try:
            count = int(self.services.clear_cache())
            text = f"✅ {count} ورودی حافظه موقت پاک شد."
        except Exception:
            text = "🟡 پاک‌سازی حافظه موقت انجام نشد."
        self._send_or_edit(chat_id, message_id, text, inline_keyboard([[("⬅️ ابزارها", "adm:tools")]]))

    def _set_update_mode(self, mode: str) -> None:
        setter = getattr(self.services, "set_update_mode", None)
        if callable(setter):
            setter(mode)
        elif hasattr(self.state, "set_update_mode"):
            self.state.set_update_mode(mode)

    def _update_mode(self) -> str:
        getter = getattr(self.services, "update_mode", None)
        if callable(getter):
            return str(getter())
        getter = getattr(self.state, "update_mode", None)
        return str(getter()) if callable(getter) else UPDATE_MODE_NOTIFY

    def _remote(self):
        try: return self.services.remote_update_info()
        except Exception: return None

    def _cached_remote(self):
        getter = getattr(self.services, "cached_remote_update_info", None)
        if callable(getter):
            try: return getter()
            except Exception: return None
        return None

    def _status(self):
        try: return self.services.update_status()
        except Exception:
            from .update_control import UpdateStatus
            return UpdateStatus(state="unknown")

    def _safe_health(self) -> dict[str, Any]:
        try:
            value = self.services.health()
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _history(self, *, limit: int):
        getter = getattr(self.services, "update_history", None)
        if not callable(getter): return ()
        try: return tuple(getter(limit=limit))
        except Exception: return ()

    def _current_sha(self) -> str | None:
        getter = getattr(self.services, "current_release_sha", None)
        if callable(getter):
            try: return getter()
            except Exception: pass
        return getattr(self._status(), "current_sha", None)

    def _previous_sha(self) -> str | None:
        getter = getattr(self.services, "previous_release_sha", None)
        if callable(getter):
            try: return getter()
            except Exception: return None
        return None

    def _claim_notification(self, sha: str, kind: str) -> bool:
        claim = getattr(self.services, "claim_update_notification", None)
        if not callable(claim): return False
        try: return bool(claim(sha, kind))
        except TypeError:
            try: return bool(claim(sha))
            except Exception: return False
        except Exception: return False

    def _bind(self, request_id: str, chat_id: int, message_id: int, target_sha: str | None) -> None:
        binder = getattr(self.services, "bind_update_progress_message", None)
        if callable(binder):
            try: binder(request_id, chat_id, message_id, target_sha=target_sha)
            except Exception: pass

    def _progress_text(self, status) -> str:
        icon, label = _STATE_META.get(str(status.state), ("⚪", "وضعیت نامشخص"))
        current = int(getattr(status, "progress_current", 0) or 0)
        total = int(getattr(status, "progress_total", 0) or 0)
        lines = ["🔄 <b>به‌روزرسانی DrJavanBot</b>", ""]
        if getattr(status, "target_sha", None): lines.append(f"نسخه: <code>{_short(status.target_sha)}</code>")
        lines.append(f"وضعیت: {icon} {html_escape(label)}")
        if total:
            lines += [f"مرحله {min(current, total)} از {total}", ""]
            for index, (stage_label, _stage_key) in enumerate(_PROGRESS_STAGES, start=1):
                if index < current: mark = "✅"
                elif index == current and status.state not in {"success", "failed", "rolled_back", "up_to_date"}: mark = "🔵"
                elif status.state in {"success", "rolled_back", "up_to_date"} and index <= max(current, total): mark = "✅"
                else: mark = "⚪"
                lines.append(f"{mark} {stage_label}")
        message = getattr(status, "message", None) or getattr(status, "stage_label", None)
        detail = getattr(status, "detail", None)
        if message: lines += ["", f"آخرین فعالیت: {html_escape(str(message))}"]
        if detail and detail != message: lines.append(html_escape(str(detail)))
        if getattr(status, "change_class", None): lines.append(f"نوع انتشار: {_change_class_label(status.change_class)}")
        if getattr(status, "error_id", None): lines.append(f"شناسه خطا: <code>{html_escape(status.error_id)}</code>")
        if getattr(status, "duration_seconds", None) is not None and status.terminal:
            lines.append(f"زمان عملیات: {float(status.duration_seconds):.1f} ثانیه")
        return "\n".join(lines)

    def _progress_keyboard(self, status) -> dict:
        if status.state == "failed":
            return inline_keyboard([[("🔄 تلاش دوباره", "adm:update:install")], [("🕘 سابقه نسخه‌ها", "adm:update:history")], [("⬅️ به‌روزرسانی", "adm:update")]])
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
            # Telegram returns an error when an explicit refresh produces exactly
            # the same rendered message. Treat it as an idempotent no-op.
            if "not modified" not in str(exc).casefold():
                raise


def _short(value) -> str:
    text = str(value or "").strip()
    return html_escape(text[:12]) if text else "—"


def _state_icon(state: str) -> str:
    return _STATE_META.get(str(state), ("⚪", ""))[0]


def _state_label(state: str) -> str:
    return _STATE_META.get(str(state), ("⚪", "نامشخص"))[1]


def _message_id(result) -> int | None:
    if not isinstance(result, dict): return None
    try:
        value = int(result.get("message_id"))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _event_time(item: dict) -> str:
    raw = item.get("completed_at") or item.get("updated_at") or item.get("started_at")
    if not raw: return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ""


def _history_result(item: dict) -> str:
    result = str(item.get("result") or item.get("state") or "").casefold()
    action = str(item.get("action") or "").casefold()
    if action == "rollback" or result == "rolled_back": return "↩️ بازگشت"
    if result in {"success", "healthy", "up_to_date"}: return "✅ موفق"
    if result in {"failed", "failure"}: return "🔴 ناموفق"
    return "⚪ ثبت‌شده"


def _change_class_label(value: str) -> str:
    return {
        "code_only": "فقط کد",
        "dependency": "وابستگی‌ها",
        "index": "کد ایندکس",
        "archive": "آرشیو",
        "mixed": "ترکیبی",
    }.get(str(value), html_escape(str(value)))


__all__ = ["AdminControlCenter", "V"]
