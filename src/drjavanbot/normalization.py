from __future__ import annotations

import re
import unicodedata

_ARABIC_TO_PERSIAN = str.maketrans({
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "ۀ": "ه",
    "ة": "ه",
    "أ": "ا",
    "إ": "ا",
    "ٱ": "ا",
    "آ": "ا",
})
_DIGITS_TO_ASCII = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_WS_RE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """Normalize Persian/English text for retrieval without mutating raw text."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(_ARABIC_TO_PERSIAN).translate(_DIGITS_TO_ASCII)
    text = text.replace("\u200c", " ").replace("\u200d", " ").replace("\ufeff", " ")
    text = text.replace("ـ", "")
    text = _DIACRITICS_RE.sub("", text)
    text = text.casefold()

    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category.startswith("P"):
            out.append(" ")
        else:
            out.append(ch)
    return _WS_RE.sub(" ", "".join(out)).strip()


def normalize_author(value: str | None) -> str | None:
    normalized = normalize_text(value)
    return normalized or None


def tokenize(value: str | None) -> tuple[str, ...]:
    normalized = normalize_text(value)
    if not normalized:
        return ()
    return tuple(token for token in normalized.split(" ") if token)
