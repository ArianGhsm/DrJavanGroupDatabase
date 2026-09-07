from __future__ import annotations
from html import escape
from typing import Iterable

MAX_TELEGRAM_TEXT = 4096
SAFE_CHUNK = 3800


def html_escape(value: object) -> str:
    return escape(str(value), quote=False)


def answer_chunks(answer) -> tuple[str, ...]:
    lines: list[str] = []
    direct = (getattr(answer, "direct_answer", "") or "").strip()
    if direct:
        lines.append(html_escape(direct))
    findings = tuple(getattr(answer, "key_findings", ()) or ())
    if findings:
        lines.append("\n<b>نکات اصلی</b>")
        lines.extend("• " + html_escape(item) for item in findings)
    disagreements = tuple(getattr(answer, "disagreements", ()) or ())
    if disagreements:
        lines.append("\n<b>اختلاف‌نظرها</b>")
        lines.extend("• " + html_escape(item) for item in disagreements)
    conclusion = getattr(answer, "practical_conclusion", None)
    if conclusion:
        lines.append("\n<b>جمع‌بندی عملی</b>")
        lines.append(html_escape(conclusion))
    confidence = str(getattr(answer, "confidence", "low") or "low").lower()
    icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "⚪️")
    reason = html_escape(getattr(answer, "confidence_reason", ""))
    lines.append(f"\n<b>اطمینان:</b> {icon} {html_escape(confidence)}" + (f" — {reason}" if reason else ""))
    safety = getattr(answer, "safety_note_if_needed", None)
    if safety:
        lines.append("\n⚕️ " + html_escape(safety))
    if bool(getattr(answer, "insufficient_evidence", False)):
        lines.append("\n<i>این پاسخ به‌دلیل کمبود شواهد آرشیو محدود است و با دانش عمومی مدل تکمیل نشده است.</i>")
    return _chunk_lines(lines)


def _chunk_lines(lines: Iterable[str], limit: int = SAFE_CHUNK) -> tuple[str, ...]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        pieces = _split_long_line(line, limit)
        for piece in pieces:
            candidate = piece if not current else current + "\n" + piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current: chunks.append(current)
                current = piece
    if current: chunks.append(current)
    return tuple(chunks or ("",))


def _split_long_line(line: str, limit: int) -> tuple[str, ...]:
    if len(line) <= limit: return (line,)
    plain = line.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    parts: list[str] = []
    while plain:
        cut = min(limit, len(plain))
        if cut < len(plain):
            ws = plain.rfind(" ", 0, cut)
            if ws > limit // 2: cut = ws
        parts.append(plain[:cut].strip())
        plain = plain[cut:].lstrip()
    return tuple(parts)


def sources_page(items: list[dict], page: int, *, per_page: int = 5) -> tuple[str, int]:
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    lines = [f"<b>منابع</b> — صفحه {page + 1}/{total_pages}"]
    for idx, item in enumerate(items[start:start+per_page], start=start+1):
        author = html_escape(item.get("author") or "نامشخص")
        date = html_escape(item.get("datetime") or "تاریخ نامشخص")
        mid = item.get("message_id")
        source = html_escape(item.get("source_file") or item.get("source_ref") or "")
        lines.append(f"\n<b>{idx}.</b> {author}\n{date}" + (f" — پیام #{mid}" if mid is not None else "") + f"\n<code>{source}</code>")
    return "\n".join(lines), total_pages


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in row] for row in rows]}
