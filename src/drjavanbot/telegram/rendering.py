from __future__ import annotations

from dataclasses import dataclass
from html import escape, unescape
import re
from typing import Iterable, Sequence

SAFE_CHUNK = 3800


@dataclass(frozen=True, slots=True)
class RichScreen:
    """Framework-free Telegram Rich Message with a safe legacy HTML fallback."""

    rich_html: str
    fallback_html: str
    screen_id: str
    is_rtl: bool = True


class RichText(str):
    """String-compatible fallback text carrying an optional Rich Message payload."""

    rich_html: str
    screen_id: str
    is_rtl: bool

    def __new__(cls, fallback_html: str, *, rich_html: str, screen_id: str, is_rtl: bool = True):
        value = str.__new__(cls, fallback_html)
        value.rich_html = rich_html
        value.screen_id = screen_id
        value.is_rtl = bool(is_rtl)
        return value


def rich_text(screen: RichScreen) -> RichText:
    return RichText(
        screen.fallback_html,
        rich_html=screen.rich_html,
        screen_id=screen.screen_id,
        is_rtl=screen.is_rtl,
    )


def html_escape(value) -> str:
    return escape(str(value or ""), quote=False)


def answer_rich_screen(answer) -> RichScreen:
    """Make the group's archive—not the model—the visible authority of an answer."""
    direct = (getattr(answer, "direct_answer", "") or "").strip()
    insufficient = bool(getattr(answer, "insufficient_evidence", False))
    if insufficient:
        rich = (
            "<h3>🔎 نتیجه جست‌وجو در گروه</h3>"
            f"<blockquote>{html_escape(direct)}</blockquote>"
            "<footer>پاسخ با دانش عمومی مدل تکمیل نشده است؛ منبع پاسخ فقط آرشیو گروه است.</footer>"
        )
        fallback = (
            "🔎 <b>نتیجه جست‌وجو در گروه</b>\n\n"
            f"{html_escape(direct)}\n\n"
            "<i>پاسخ با دانش عمومی مدل تکمیل نشده است؛ منبع پاسخ فقط آرشیو گروه است.</i>"
        )
        return RichScreen(rich, fallback, "archive_answer_insufficient")

    findings = tuple(getattr(answer, "key_findings", ()) or ())
    disagreements = tuple(getattr(answer, "disagreements", ()) or ())
    conclusion = getattr(answer, "practical_conclusion", None)
    confidence = str(getattr(answer, "confidence", "low") or "low").casefold()
    reason = str(getattr(answer, "confidence_reason", "") or "").strip()
    safety = getattr(answer, "safety_note_if_needed", None)

    rich_parts = [
        "<h3>📚 جمع‌بندی پیام‌های گروه</h3>",
        f"<blockquote>{html_escape(direct)}</blockquote>",
    ]
    fallback_parts = ["📚 <b>جمع‌بندی پیام‌های گروه</b>", html_escape(direct)]
    if findings:
        rich_parts.extend((
            "<h3>آنچه در گروه گفته شده</h3>",
            "<ul>" + "".join(f"<li>{html_escape(item)}</li>" for item in findings) + "</ul>",
        ))
        fallback_parts.extend((
            "<b>آنچه در گروه گفته شده</b>",
            "\n".join(f"• {html_escape(item)}" for item in findings),
        ))
    if disagreements:
        rich_parts.extend((
            "<h3>⚖️ اختلاف‌نظر در پیام‌ها</h3>",
            "<ul>" + "".join(f"<li>{html_escape(item)}</li>" for item in disagreements) + "</ul>",
        ))
        fallback_parts.extend((
            "<b>⚖️ اختلاف‌نظر در پیام‌ها</b>",
            "\n".join(f"• {html_escape(item)}" for item in disagreements),
        ))
    if conclusion:
        rich_parts.extend((
            "<h3>جمع‌بندی پشتیبانی‌شده</h3>",
            f"<p>{html_escape(conclusion)}</p>",
        ))
        fallback_parts.extend(("<b>جمع‌بندی پشتیبانی‌شده</b>", html_escape(conclusion)))

    coverage = {"high": "زیاد", "medium": "متوسط", "low": "محدود"}.get(confidence, "محدود")
    coverage_text = coverage + (f" — {reason}" if reason else "")
    rich_parts.extend((
        "<hr>",
        "<h3>📎 پوشش شواهد گروه</h3>",
        f"<p>{html_escape(coverage_text)}</p>",
    ))
    fallback_parts.extend(("<b>📎 پوشش شواهد گروه</b>", html_escape(coverage_text)))
    if safety:
        rich_parts.append(f"<blockquote>{html_escape(safety)}</blockquote>")
        fallback_parts.append(f"<i>{html_escape(safety)}</i>")
    rich_parts.append(
        "<footer>منبع پاسخ فقط آرشیو گروه دکتر جوان است؛ هوش مصنوعی فقط برای جست‌وجو و خلاصه‌سازی پیام‌های بازیابی‌شده استفاده شده است.</footer>"
    )
    fallback_parts.append(
        "<i>منبع پاسخ فقط آرشیو گروه دکتر جوان است؛ هوش مصنوعی فقط برای جست‌وجو و خلاصه‌سازی پیام‌های بازیابی‌شده استفاده شده است.</i>"
    )
    return RichScreen("".join(rich_parts), "\n\n".join(fallback_parts), "archive_answer")


def answer_chunks(answer):
    """Return one rich-capable string when possible; retain safe legacy chunking."""
    screen = answer_rich_screen(answer)
    if len(screen.rich_html) <= 32000:
        return (rich_text(screen),)
    return _chunk_lines(screen.fallback_html.split("\n"))


def _chunk_lines(lines: Iterable[str], limit: int = SAFE_CHUNK):
    chunks = []
    current = ""
    for line in lines:
        for piece in _split_long_line(line, limit):
            candidate = piece if not current else current + "\n" + piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return tuple(chunks or ("",))


def _split_long_line(line, limit):
    if len(line) <= limit:
        return (line,)
    plain = re.sub(r"</?(?:b|i|code)>", "", line, flags=re.IGNORECASE)
    parts = []
    while plain:
        cut = min(limit, len(plain))
        ws = plain.rfind(" ", 0, cut) if cut < len(plain) else -1
        if ws > limit // 2:
            cut = ws
        parts.append(plain[:cut].strip())
        plain = plain[cut:].lstrip()
    return tuple(parts)


def _sources_fallback(items, page, *, per_page=5):
    total = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total - 1))
    start = page * per_page
    lines = [f"<b>پیام‌های منبع</b> — صفحه {page + 1}/{total}"]
    for idx, item in enumerate(items[start:start + per_page], start=start + 1):
        author = html_escape(item.get("author") or "نامشخص")
        date = html_escape(item.get("datetime") or "تاریخ نامشخص")
        mid = item.get("message_id")
        source = html_escape(item.get("source_file") or item.get("source_ref") or "")
        excerpt = item.get("text") or item.get("text_excerpt") or ""
        excerpt_line = f"\n{html_escape(excerpt)}" if excerpt else ""
        lines.append(
            f"\n<b>{idx}.</b> {author}\n{date}"
            + (f" — پیام #{mid}" if mid is not None else "")
            + excerpt_line
            + f"\n<code>{source}</code>"
        )
    return "\n".join(lines), total


def sources_rich_screen(items, page, *, per_page=5):
    fallback, total = _sources_fallback(items, page, per_page=per_page)
    page = max(0, min(page, total - 1))
    start = page * per_page
    rich = [f"<h3>📚 پیام‌های منبع — {page + 1}/{total}</h3>"]
    for idx, item in enumerate(items[start:start + per_page], start=start + 1):
        author = html_escape(item.get("author") or "نامشخص")
        date = html_escape(item.get("datetime") or "تاریخ نامشخص")
        mid = item.get("message_id")
        excerpt = html_escape(item.get("text") or item.get("text_excerpt") or "")
        source = html_escape(item.get("source_file") or item.get("source_ref") or "")
        heading = f"{idx}. {author} — {date}" + (f" — پیام #{mid}" if mid is not None else "")
        rich.append(f"<h3>{heading}</h3>")
        if excerpt:
            rich.append(f"<blockquote>{excerpt}</blockquote>")
        rich.append(f"<footer>{source}</footer>")
    return RichScreen("".join(rich), fallback, "archive_sources"), total


def sources_page(items, page, *, per_page=5):
    screen, total = sources_rich_screen(items, page, per_page=per_page)
    return rich_text(screen), total


def inline_keyboard(rows: Sequence[Sequence[tuple[str, str]]]):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)


def html_to_plain(value: str) -> str:
    return unescape(_TAG_RE.sub("", str(value)))
