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


def informative_tokens(value: str | None) -> tuple[str, ...]:
    """Return stable topical tokens while suppressing question/filler words."""
    out: list[str] = []
    seen: set[str] = set()
    for token in tokenize(value):
        if token in _LOW_INFORMATION_TOKENS or len(token) <= 1 or token in seen:
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


__all__ = ["distinctive_terms", "informative_query", "informative_tokens", "is_low_information_token"]
