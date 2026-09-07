from __future__ import annotations

import re
from collections.abc import Iterable

from drjavanbot.normalization import normalize_text, tokenize

# Query-language words carry little topical information. They may still be useful
# to the AI planner as intent, but they should never dominate lexical retrieval.
_LOW_INFORMATION_TOKENS = frozenset(
    normalize_text(value)
    for value in (
        "چرا", "چی", "چیه", "چیس", "کدوم", "کدام", "آیا", "ایا", "خوبه", "خوب", "بهتره",
        "بهترین", "بگو", "بگید", "بگین", "کسی", "میشه", "میشود", "می شود", "هست", "هستش",
        "است", "بود", "رو", "را", "این", "اون", "آن", "برای", "در", "از", "به", "با", "و", "یا",
        "که", "من", "ما", "شما", "لطفا", "لطفاً", "what", "which", "why", "is", "are", "was",
        "the", "a", "an", "good", "best", "please", "tell", "me",
    )
)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)
_SINGLE_LATIN_RE = re.compile(r"^[a-z]$", re.IGNORECASE)
_LATIN_ALNUM_RE = re.compile(r"[a-z0-9]", re.IGNORECASE)


def informative_tokens(value: str | None) -> tuple[str, ...]:
    """Return stable topical tokens while suppressing question/filler words.

    Single-character tokens are normally noise. A single Latin letter is retained
    only when immediately adjacent to another informative Latin/alphanumeric token,
    preserving common compound notation such as ``e.max`` -> ``e max`` and
    ``x-ray`` -> ``x ray`` without re-admitting Persian conjunctions or English
    articles such as ``a``.
    """
    tokens = tokenize(value)
    out: list[str] = []
    seen: set[str] = set()
    for index, token in enumerate(tokens):
        if token in _LOW_INFORMATION_TOKENS or token in seen:
            continue
        if len(token) <= 1 and not _meaningful_single_latin(tokens, index):
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


def informative_query(value: str | None) -> str:
    return " ".join(informative_tokens(value))


def is_low_information_token(value: str) -> bool:
    token = normalize_text(value)
    return not token or token in _LOW_INFORMATION_TOKENS


def distinctive_terms(values: Iterable[str | None], *, exclude: Iterable[str] = (), limit: int = 32) -> tuple[str, ...]:
    """Extract bounded corpus vocabulary for adaptive search refinement.

    This is deliberately vocabulary-only: returned terms are search hints, never
    evidence. Latin/mixed product-like tokens are preferred, followed by longer
    Persian terms, and generic query-language words are removed.
    """
    excluded = set(informative_tokens(" ".join(str(x) for x in exclude)))
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    ordinal = 0
    for value in values:
        for token in informative_tokens(value):
            if token in excluded or len(token) < 3:
                continue
            counts[token] = counts.get(token, 0) + 1
            first_seen.setdefault(token, ordinal)
            ordinal += 1
    ranked = sorted(
        counts,
        key=lambda term: (
            -int(bool(_LATIN_RE.search(term))),
            -min(counts[term], 4),
            -min(len(term), 18),
            first_seen[term],
            term,
        ),
    )
    return tuple(ranked[: max(1, min(limit, 64))])


def _meaningful_single_latin(tokens: tuple[str, ...], index: int) -> bool:
    token = tokens[index]
    if token in _LOW_INFORMATION_TOKENS or not _SINGLE_LATIN_RE.fullmatch(token):
        return False
    for neighbor_index in (index - 1, index + 1):
        if not 0 <= neighbor_index < len(tokens):
            continue
        neighbor = tokens[neighbor_index]
        if neighbor in _LOW_INFORMATION_TOKENS or len(neighbor) < 2:
            continue
        if _LATIN_ALNUM_RE.search(neighbor):
            return True
    return False


__all__ = ["distinctive_terms", "informative_query", "informative_tokens", "is_low_information_token"]
