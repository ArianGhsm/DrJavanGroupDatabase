from __future__ import annotations

from drjavanbot.normalization import normalize_text

# Persian cardinal/ordinal words ↔ digits. Archive authors mix "نسل ۷",
# "نسل هفت" and "نسل هفتم" freely, so retrieval treats them as one token.
_WORDS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "یک", ("اول", "یکم")),
    (2, "دو", ("دوم",)),
    (3, "سه", ("سوم",)),
    (4, "چهار", ("چهارم",)),
    (5, "پنج", ("پنجم",)),
    (6, "شش", ("ششم", "شیش", "شیشم")),
    (7, "هفت", ("هفتم",)),
    (8, "هشت", ("هشتم",)),
    (9, "نه", ("نهم",)),
    (10, "ده", ("دهم",)),
    (11, "یازده", ("یازدهم",)),
    (12, "دوازده", ("دوازدهم",)),
    (13, "سیزده", ("سیزدهم",)),
    (14, "چهارده", ("چهاردهم",)),
    (15, "پانزده", ("پانزدهم", "پونزده")),
    (16, "شانزده", ("شانزدهم", "شونزده")),
    (17, "هفده", ("هفدهم",)),
    (18, "هجده", ("هجدهم", "هیجده")),
    (19, "نوزده", ("نوزدهم",)),
    (20, "بیست", ("بیستم",)),
)
# "نه" (no) and "ده" (village) are too ambiguous to *emit* as alternates of a
# digit, but a user who types the ordinal "نهم"/"دهم" still matches digits.
_AMBIGUOUS_CARDINALS = frozenset({"نه", "ده", "دو", "سه"})

_BY_TOKEN: dict[str, tuple[str, ...]] = {}
for _value, _cardinal, _others in _WORDS:
    _digit = str(_value)
    _forms = tuple(normalize_text(v) for v in (_cardinal, *_others))
    _emit = tuple(f for f in _forms if f not in _AMBIGUOUS_CARDINALS)
    _BY_TOKEN[_digit] = _emit
    for _form in _forms:
        if _form in _AMBIGUOUS_CARDINALS:
            continue
        _BY_TOKEN[_form] = tuple(dict.fromkeys((_digit, *(f for f in _emit if f != _form))))


def number_variants(token: str) -> tuple[str, ...]:
    """Return equivalent spellings of a numeric token (never the token itself)."""
    return _BY_TOKEN.get(normalize_text(token), ())


def colloquial_variants(token: str) -> tuple[str, ...]:
    """Spoken/written vowel alternation: "دندون" ↔ "دندان", "دندونا" is left alone.

    Only the final "ان"/"ون" of a Persian word of 4+ letters is alternated; the
    alternate is an OR-equivalent, so a form absent from the archive is inert.
    """
    value = normalize_text(token)
    if len(value) < 4 or not ("\u0600" <= value[0] <= "\u06ff"):
        return ()
    if value.endswith("ان"):
        return (value[:-2] + "ون",)
    if value.endswith("ون"):
        return (value[:-2] + "ان",)
    return ()


__all__ = ["colloquial_variants", "number_variants"]
