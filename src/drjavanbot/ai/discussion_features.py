from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Sequence
from drjavanbot.normalization import normalize_text, tokenize
from drjavanbot.search import EvidenceCandidate
from .discussion_types import _HitState

CORRECTION_CUES = frozenset(normalize_text(value) for value in (
    "اصلاح", "تصحیح", "اشتباه", "درستش", "نه", "اما", "ولی", "actually", "correction", "wrong", "but"
))

def _candidate_from_state(
    state: _HitState,
    *,
    score: float,
    extra_reasons: Iterable[str],
    cluster_key: str | None,
    cluster_size: int,
) -> EvidenceCandidate:
    reasons = set(state.match_reasons)
    reasons.update(extra_reasons)
    reasons.add(f"family_coverage:{len(state.family_hits)}")
    for family in state.family_hits:
        reasons.add(f"hit_family:{family}")
    return replace(
        state.candidate,
        local_score=round(score, 6),
        matched_terms=tuple(sorted(state.matched_terms)),
        match_reasons=tuple(sorted(reasons)),
        cluster_key=cluster_key,
        cluster_size=cluster_size,
    )


def _discussion_span(states: Sequence[_HitState]) -> int:
    if not states:
        return 0
    pages = {state.candidate.message.source_page for state in states}
    if len(pages) != 1:
        return 0
    orders = [state.candidate.message.source_order for state in states]
    return max(orders) - min(orders)


def _discussion_key(states: Sequence[_HitState], representative: _HitState) -> str:
    message = representative.candidate.message
    member_ids = sorted(_message_key(state.candidate) for state in states)
    seed = member_ids[0] if member_ids else _message_key(representative.candidate)
    return f"discussion:{message.source_page}:{message.source_order // 8}:{seed}"


def _message_key(candidate: EvidenceCandidate) -> str:
    message = candidate.message
    return message.source_locator or f"{message.source_page}:{message.source_order}:{message.message_id}"


def _context_dependent(candidate: EvidenceCandidate) -> bool:
    text = normalize_text(candidate.message.text_normalized or candidate.message.text_raw)
    return bool(
        candidate.message.reply_to_message_id is not None
        or len(text) <= 56
        or len(tokenize(text)) <= 6
    )


def _correction_cue_count(candidate: EvidenceCandidate) -> int:
    tokens = set(tokenize(candidate.message.text_normalized or candidate.message.text_raw))
    return sum(1 for cue in CORRECTION_CUES if cue and cue in tokens)


def _family_coverage(reasons: Sequence[str]) -> int:
    for prefix in ("discussion_family_coverage:", "family_coverage:"):
        for reason in reasons:
            if reason.startswith(prefix):
                try:
                    return int(reason.split(":", 1)[1])
                except ValueError:
                    return 0
    return 0
