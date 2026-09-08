from __future__ import annotations

from collections import defaultdict
from typing import Sequence
from drjavanbot.search import EvidenceCandidate
from .planner import SearchPlan
from .discussion_types import DiscussionCandidate, MAX_DISCUSSIONS


def _extract_evidence_candidates(
    discussions: Sequence[DiscussionCandidate],
    *,
    limit: int,
) -> tuple[EvidenceCandidate, ...]:
    bounded = max(1, min(int(limit), 120))
    selected: list[EvidenceCandidate] = []
    author_counts: dict[str, int] = defaultdict(int)
    page_bucket_counts: dict[tuple[int, int], int] = defaultdict(int)
    remaining = list(discussions[:MAX_DISCUSSIONS])

    while remaining and len(selected) < bounded:
        best_index = 0
        best_effective = float("-inf")
        for index, discussion in enumerate(remaining[:120]):
            candidate = discussion.representative
            author = candidate.message.author_normalized or candidate.message.author or ""
            bucket = (candidate.message.source_page, candidate.message.source_order // 8)
            effective = discussion.score
            if discussion.facet_complete:
                effective += 0.32
            if discussion.topic_anchored:
                effective += 0.20
            if author:
                effective -= min(0.75, author_counts[author] * 0.22)
            effective -= min(0.65, page_bucket_counts[bucket] * 0.22)
            if effective > best_effective:
                best_effective = effective
                best_index = index
        chosen = remaining.pop(best_index)
        candidate = chosen.representative
        selected.append(candidate)
        author = candidate.message.author_normalized or candidate.message.author or ""
        if author:
            author_counts[author] += 1
        page_bucket_counts[(candidate.message.source_page, candidate.message.source_order // 8)] += 1
    return tuple(selected)


def _quality_state(
    plan: SearchPlan,
    discussions: Sequence[DiscussionCandidate],
    candidates: Sequence[EvidenceCandidate],
    *,
    required_groups: Sequence[set[str]],
) -> str:
    if not candidates or not discussions:
        return "no_candidates"
    top = discussions[:8]
    if any(item.facet_complete for item in top):
        return "facet_complete_discussion"
    if required_groups and any(item.topic_anchored for item in top):
        return "only_topical_facet_missing"
    if not any(item.topic_anchored for item in top):
        return "generic_noisy_coverage"
    if (
        len(plan.query_families) <= 1
        and top[0].topic_anchored
        and top[0].representative.cluster_size == 1
        and "discussion_exact_anchor" in top[0].ranking_reasons
    ):
        # A true one-message exact lookup (e.g. a direct product/material name)
        # can stop early. If several messages were clustered under a fallback
        # single-family plan, keep the existing bounded refinement opportunity;
        # clustering alone must not manufacture direct-answer confidence.
        return "strong_direct_answer_candidate"
    return "topical_coverage"
