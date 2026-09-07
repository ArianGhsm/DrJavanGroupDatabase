from __future__ import annotations

import threading
import time
from typing import Mapping

from .api import TelegramAPIError
from .rendering import RichScreen, html_escape, rich_text


_STAGE_STEP = {
    "accepted": 0,
    "planning": 1,
    "searching": 2,
    "refining": 2,
    "context": 3,
    "synthesizing": 4,
    "validating": 4,
    "repairing": 4,
    "cache_hit": 4,
    "no_evidence": 4,
    "validation_failed": 4,
    "done": 5,
}
_STEPS = (
    "فهم سؤال",
    "جست‌وجوی آرشیو",
    "بررسی گفت‌وگوهای مرتبط",
    "جمع‌بندی و اعتبارسنجی شواهد",
)
_HEARTBEAT_SECONDS = 5.0


class QuestionProgressReporter:
    """One fail-soft status message that follows the real answer pipeline.

    This is deliberately not a chain-of-thought transport. It receives only the
    safe operational milestones emitted by ``ArchiveAnswerService`` and never
    model prompts, hidden reasoning, unvalidated excerpts or user secrets.

    A lightweight heartbeat refreshes only elapsed time and the Telegram typing
    action while a slow provider call is in flight. It never invokes retrieval or
    AI and therefore cannot duplicate business work.
    """

    def __init__(self, api, chat_id: int) -> None:
        self.api = api
        self.chat_id = int(chat_id)
        self.message_id: int | None = None
        self.started_at = time.monotonic()
        self.last_stage = "accepted"
        self.last_details: dict[str, object] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def start(self) -> None:
        self._typing()
        try:
            result = self.api.send_message(
                self.chat_id,
                rich_text(_progress_screen("accepted", {}, elapsed=0.0)),
            )
        except TelegramAPIError:
            return
        except Exception:
            return
        if isinstance(result, Mapping):
            try:
                message_id = int(result.get("message_id"))
            except (TypeError, ValueError):
                message_id = None
            with self._lock:
                self.message_id = message_id
        if self.message_id is not None:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat,
                name=f"drjavan-progress-{self.chat_id}",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def on_event(self, stage: str, details: dict[str, object]) -> None:
        stage = str(stage or "").strip().casefold()
        if stage not in _STAGE_STEP:
            return
        with self._lock:
            self.last_stage = stage
            self.last_details = dict(details or {})
        self._typing()
        self._refresh()

    def close(self) -> None:
        """Remove the transient status after the final answer/error is visible."""
        self._stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        with self._lock:
            message_id = self.message_id
            self.message_id = None
        if message_id is None:
            return
        try:
            self.api.delete_message(self.chat_id, message_id)
        except Exception:
            pass

    def _heartbeat(self) -> None:
        while not self._stop.wait(_HEARTBEAT_SECONDS):
            self._typing()
            self._refresh()

    def _refresh(self) -> None:
        with self._lock:
            message_id = self.message_id
            stage = self.last_stage
            details = dict(self.last_details)
        if message_id is None or self._stop.is_set():
            return
        try:
            self.api.edit_message_text(
                self.chat_id,
                message_id,
                rich_text(
                    _progress_screen(
                        stage,
                        details,
                        elapsed=max(0.0, time.monotonic() - self.started_at),
                    )
                ),
            )
        except TelegramAPIError:
            # Progress presentation must never fail or repeat the answer job.
            return
        except Exception:
            return

    def _typing(self) -> None:
        try:
            self.api.send_chat_action(self.chat_id, "typing")
        except Exception:
            pass


def _progress_screen(stage: str, details: Mapping[str, object], *, elapsed: float) -> RichScreen:
    current = _STAGE_STEP.get(stage, 0)
    rows: list[str] = []
    fallback_rows: list[str] = []
    for index, label in enumerate(_STEPS, start=1):
        if current >= 5 or index < current:
            icon = "✅"
        elif index == current:
            icon = "🔵"
        else:
            icon = "▫️"
        rows.append(f"<li>{icon} {html_escape(label)}</li>")
        fallback_rows.append(f"{icon} {label}")

    detail = _stage_detail(stage, details)
    seconds = int(max(0.0, elapsed))
    time_label = f"{seconds} ثانیه" if seconds else "شروع"
    rich = (
        "<h3>🔎 در حال بررسی آرشیو گروه</h3>"
        "<ul>" + "".join(rows) + "</ul>"
        f"<blockquote>{html_escape(detail)}</blockquote>"
        f"<footer>زمان سپری‌شده: {html_escape(time_label)} — این فقط وضعیت واقعی pipeline است، نه chain-of-thought داخلی مدل.</footer>"
    )
    fallback = (
        "🔎 <b>در حال بررسی آرشیو گروه</b>\n\n"
        + "\n".join(fallback_rows)
        + f"\n\n<i>{html_escape(detail)}</i>"
        + f"\n\n<code>{html_escape(time_label)}</code>"
    )
    return RichScreen(rich, fallback, "question_progress")


def _stage_detail(stage: str, details: Mapping[str, object]) -> str:
    if stage == "accepted":
        return "سؤال دریافت شد؛ پردازش شروع شده است."
    if stage == "planning":
        if bool(details.get("cached")):
            return "برنامه جست‌وجوی معتبر قبلی پیدا شد؛ آماده جست‌وجوی آرشیو است."
        return "در حال تبدیل سؤال به مسیرهای محدود جست‌وجو؛ این مرحله پاسخ تولید نمی‌کند."
    if stage == "searching":
        queries = _int(details.get("query_count"))
        families = _int(details.get("family_count"))
        suffix = " تکمیلی" if bool(details.get("refined")) else ""
        if queries or families:
            return f"جست‌وجوی{suffix} محلی در {families or 1} خانواده و {queries or 1} کوئری در حال اجراست."
        return f"جست‌وجوی{suffix} محلی در آرشیو در حال اجراست."
    if stage == "context":
        candidates = _int(details.get("candidate_count"))
        authors = _int(details.get("author_count"))
        windows = _int(details.get("discussion_windows"))
        hydrated = _int(details.get("context_hydrated"))
        pieces = [f"{candidates} نتیجه اولیه", f"{authors} نویسنده"]
        if windows:
            pieces.append(f"{windows} بازه گفت‌وگویی")
        elif hydrated:
            pieces.append(f"context برای {hydrated} نتیجه")
        return "، ".join(pieces) + "؛ پیام‌های قبل/بعد و replyها به‌صورت محدود بررسی می‌شوند."
    if stage == "refining":
        return "پوشش جست‌وجوی اول کافی نبود؛ یک جست‌وجوی تکمیلی محدود با واژگان خود آرشیو انجام می‌شود."
    if stage == "synthesizing":
        messages = _int(details.get("evidence_messages"))
        authors = _int(details.get("evidence_authors"))
        return f"{messages} پیام از {authors} نویسنده وارد بسته شواهد شده؛ جمع‌بندی فقط از همین پیام‌ها ساخته می‌شود."
    if stage == "validating":
        return "در حال تطبیق هر ادعا با message_id و نقل‌قول واقعی همان پیام گروه."
    if stage == "repairing":
        return "قالب پاسخ AI معتبر نبود؛ یک تلاش اصلاحی محدود انجام می‌شود و grounding همچنان اجباری است."
    if stage == "cache_hit":
        return "نسخه مستند و معتبر همین پاسخ در cache پیدا شد؛ فراخوانی AI جدید لازم نیست."
    if stage == "no_evidence":
        return "جست‌وجو تمام شد؛ شواهد کافی در پیام‌های گروه پیدا نشد."
    if stage == "validation_failed":
        return "خروجی AI نتوانست از اعتبارسنجی سخت شواهد گروه عبور کند."
    if stage == "done":
        return "پاسخ آماده شد و فقط شواهد تأییدشده گروه در آن مجاز است."
    return "پردازش ادامه دارد."


def _int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


__all__ = ["QuestionProgressReporter"]
