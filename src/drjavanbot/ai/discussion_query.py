from __future__ import annotations

from typing import Sequence
from drjavanbot.normalization import normalize_text, tokenize
from drjavanbot.search import EvidenceCandidate
from .discussion_types import RRF_K, _FamilyHit, _HitState

def _collect_message_states(
    runs: Sequence[tuple[str, str, Sequence[EvidenceCandidate]]],
) -> tuple[_HitState, ...]:
    state: dict[str, _HitState] = {}
    for family, query_text, candidates in runs:
        values = tuple(candidates)
        if not values:
            continue
        max_local = max(max(candidate.local_score, 0.0) for candidate in values) or 1.0
        for rank, candidate in enumerate(values, start=1):
            key = _message_key(candidate)
            item = state.get(key)
            if item is None:
                item = _HitState(
                    candidate=candidate,
                    family_hits={},
                    matched_terms=set(candidate.matched_terms),
                    match_reasons=set(candidate.match_reasons),
                )
                state[key] = item
            else:
                item.matched_terms.update(candidate.matched_terms)
                item.match_reasons.update(candidate.match_reasons)
                current = item.candidate
                if (
                    candidate.local_score > current.local_score
                    or len(candidate.context) > len(current.context)
                ):
                    item.candidate = candidate

            local_norm = min(1.0, max(candidate.local_score, 0.0) / max_local)
            rrf = RRF_K / (RRF_K + rank)
            qualified = _query_hit_is_qualified(query_text, candidate)
            incoming = _FamilyHit(rank, rrf, local_norm, candidate.local_score, qualified)
            previous = item.family_hits.get(family)
            if previous is None or (
                int(incoming.qualified),
                incoming.local_norm + incoming.rrf,
                incoming.local_score,
                -incoming.rank,
            ) > (
                int(previous.qualified),
                previous.local_norm + previous.rrf,
                previous.local_score,
                -previous.rank,
            ):
                item.family_hits[family] = incoming
    return tuple(state.values())


def _query_hit_is_qualified(query_text: str, candidate: EvidenceCandidate) -> bool:
    query_tokens = tokenize(query_text)
    if not query_tokens:
        return False
    if len(query_tokens) == 1:
        return True
    reasons = set(candidate.match_reasons)
    if "exact_phrase" in reasons or "normalized_tokens" in reasons:
        return True
    direct = normalize_text(candidate.message.text_normalized or candidate.message.text_raw)
    return all(token in direct for token in query_tokens)


def _query_surface_variants(value: str) -> tuple[str, ...]:
    """Generate only orthographic Latin/alnum variants, never answer facts."""
    tokens = tokenize(value)
    if not 2 <= len(tokens) <= 4:
        return ()
    if not all(token.isascii() and token.isalnum() for token in tokens):
        return ()
    compact = "".join(tokens)
    normalized = normalize_text(value)
    if compact and compact != normalized and 3 <= len(compact) <= 32:
        return (compact,)
    return ()


def _message_key(candidate: EvidenceCandidate) -> str:
    message = candidate.message
    return message.source_locator or f"{message.source_page}:{message.source_order}:{message.message_id}"
