"""Mask obvious personal data before archive text is sent to an external model.

Covers mobile and landline numbers (Latin, Persian and Arabic-Indic digits,
with spaces/dashes), e-mail addresses and Telegram invite links, as required
by docs/architecture/ADR-003-ai-evidence-pipeline.md.
"""
from __future__ import annotations

import re

_D = "[0-9۰-۹٠-٩]"
_SEP = r"[\s\-‐–.]?"
# Iranian mobile: optional +98/0098/0 then 9 and nine digits, separators allowed.
_MOBILE_RE = re.compile(rf"(?<![0-9۰-۹٠-٩])(?:(?:\+|00)?{_D}{_D}{_SEP}|{_D})?9{_D}{{2}}{_SEP}{_D}{{3}}{_SEP}{_D}{{2}}{_SEP}{_D}{{2}}(?![0-9۰-۹٠-٩])")
# Landline: 0 + 2-3 digit area code + 7-8 digits.
_LANDLINE_RE = re.compile(rf"(?<![0-9۰-۹٠-٩])[0۰٠]{_D}{{2,3}}{_SEP}{_D}{{3,4}}{_SEP}{_D}{{4}}(?![0-9۰-۹٠-٩])")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_INVITE_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)[^\s<]+", re.I)


def redact(text: str) -> str:
    text = _INVITE_RE.sub("[لینک دعوت حذف شد]", text)
    text = _EMAIL_RE.sub("[ایمیل حذف شد]", text)
    text = _MOBILE_RE.sub("[شماره تماس حذف شد]", text)
    return _LANDLINE_RE.sub("[شماره تماس حذف شد]", text)


__all__ = ["redact"]
