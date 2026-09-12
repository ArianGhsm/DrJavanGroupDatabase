from __future__ import annotations

from typing import Sequence
from .discussion_types import DiscussionCandidate, _HitState, MAX_TOPIC_BRIDGE_DISTANCE
from .discussion_features import (
    _candidate_from_state, _context_dependent, _correction_cue_count,
    _discussion_key, _discussion_span, _message_key,
)
from .discussion_links import _direct_reply_related, _same_page_distance


def _make_discussion(
    states: Sequence[_HitState],
    *,
    anchor_families: set[str],
    required_groups: Sequence[set[str]],
    anchored: bool,
    anchor_exists: bool = False,
) -> DiscussionCandidate:
    values = tuple(states)
    qualified_families = set().union(*(state.qualified_families for state in values)) if values else set()
    family_names = set().union(*(state.families for state in values)) if values else set()
    present_anchors = (
        set().union(*(state.families for state in values if state.topic_anchor))
        if anchored else set()
    )
    facet_families = qualified_families - anchor_families
    required_hit = sum(1 for group in required_groups if qualified_families & group)

    representative_state = max(
        values,
        key=lambda state: (
            int(state.topic_anchor),
            state.base_score,
            int("exact_phrase" in state.match_reasons),
            len(state.family_hits),
            -state.candidate.message.source_page,
            -state.candidate.message.source_order,
        ),
    )
    reply_edges = 0
    proximity_edges = 0
    for left in range(len(values)):
        for right in range(left + 1, len(values)):
            if _direct_reply_related(values[left].candidate, values[right].candidate):
                reply_edges += 1
            elif _same_page_distance(values[left].candidate, values[right].candidate) <= MAX_TOPIC_BRIDGE_DISTANCE:
                proximity_edges += 1

    authors = {
        state.candidate.message.author_normalized or state.candidate.message.author
        for state in values
        if state.candidate.message.author_normalized or state.candidate.message.author
    }
    span = _discussion_span(values)
    correction_cues = sum(_correction_cue_count(state.candidate) for state in values)

    member_scores = sorted((state.base_score for state in values), reverse=True)
    score = member_scores[0] if member_scores else 0.0
    score += 0.22 * min(3, max(0, len(member_scores) - 1))
    reasons: set[str] = set()

    if present_anchors:
        score += 1.35
        reasons.add("discussion_topic_anchor")
        # Backward-compatible aliases remain useful to the existing orchestrator
        # and telemetry while the richer discussion_* reasons carry the v2 detail.
        reasons.add("anchor_family_hit")
    elif anchor_exists:
        score -= 1.75
        reasons.add("discussion_unanchored_penalty")

    if required_hit:
        score += 1.00 * required_hit
        reasons.add(f"discussion_required_facet_coverage:{required_hit}")
    if required_groups and required_hit >= len(required_groups) and present_anchors:
        score += 0.85
        reasons.add("discussion_facet_complete")
    if len(qualified_families) > 1:
        score += 0.24 * min(4, len(qualified_families) - 1)
        reasons.add(f"discussion_family_coverage:{len(qualified_families)}")
    if present_anchors and facet_families:
        # Legacy name: this now means a facet family was actually co-located
        # inside a topic-anchored discussion, not merely somewhere nearby globally.
        reasons.add("conversation_bridge")
    if reply_edges:
        score += min(0.75, 0.42 + 0.10 * (reply_edges - 1))
        reasons.add("discussion_reply_edge")
        reasons.add("reply_context")
    elif representative_state.candidate.message.reply_to_message_id is not None:
        reasons.add("reply_context")
    if proximity_edges:
        score += min(0.45, 0.14 + 0.06 * (proximity_edges - 1))
        reasons.add("discussion_proximity")
    if len(authors) > 1:
        score += min(0.42, 0.12 * (len(authors) - 1))
        reasons.add("discussion_author_diversity")
    if any("exact_phrase" in state.match_reasons for state in values if state.topic_anchor):
        score += 0.36
        reasons.add("discussion_exact_anchor")
    short_count = sum(_context_dependent(state.candidate) for state in values)
    if short_count:
        reasons.add(f"discussion_context_dependent:{short_count}")
    if correction_cues:
        reasons.add(f"discussion_correction_cues:{min(correction_cues, 9)}")
    if span > MAX_TOPIC_BRIDGE_DISTANCE:
        score -= min(0.60, (span - MAX_TOPIC_BRIDGE_DISTANCE) * 0.06)
        reasons.add(f"discussion_span:{span}")

    representative = _candidate_from_state(
        representative_state,
        score=score,
        extra_reasons=reasons,
        cluster_key=_discussion_key(values, representative_state),
        cluster_size=len(values),
    )
    members = tuple(
        _candidate_from_state(
            state,
            score=state.base_score,
            extra_reasons=(),
            cluster_key=representative.cluster_key,
            cluster_size=len(values),
        )
        for state in sorted(
            values,
            key=lambda item: (
                -int(item.topic_anchor),
                -item.base_score,
                item.candidate.message.source_page,
                item.candidate.message.source_order,
            ),
        )
    )
    return DiscussionCandidate(
        key=representative.cluster_key or _message_key(representative),
        representative=representative,
        members=members,
        family_names=tuple(sorted(family_names)),
        anchor_families=tuple(sorted(present_anchors)),
        facet_families=tuple(sorted(facet_families)),
        required_facet_groups_hit=required_hit,
        required_facet_groups_total=len(required_groups),
        reply_edges=reply_edges,
        proximity_edges=proximity_edges,
        author_count=len(authors),
        correction_cues=correction_cues,
        span=span,
        score=round(score, 6),
        ranking_reasons=tuple(sorted(reasons)),
    )
