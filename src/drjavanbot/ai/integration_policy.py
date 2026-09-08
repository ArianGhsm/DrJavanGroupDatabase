from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from .reasoning import AnswerabilityAssessment, assess_answerability

INTEGRATION_POLICY_VERSION = "brain-v2-integration-policy-v2"

_COMPLETE_STATES = {"facet_complete_discussion", "strong_direct_answer_candidate"}
_RESCUE_STATES = {"only_topical_facet_missing", "generic_noisy_coverage", "no_candidates"}


def should_refine_retrieval(plan, report, *, legacy_needs_refinement: bool, legacy_reason: str) -> tuple[bool, str]:
    """Reconcile typed planner depth with discussion-retrieval quality."""
    policy = getattr(plan, "retrieval_policy", None)
    rescue_allowed = bool(getattr(policy, "rescue_allowed", True))
    if not rescue_allowed:
        return False, "planner_rescue_disabled"

    quality = str(getattr(report, "quality_state", "") or "")
    stop_when_complete = bool(getattr(policy, "stop_when_required_facets_covered", True))
    if quality in _COMPLETE_STATES and stop_when_complete:
        return False, quality
    if quality in _RESCUE_STATES:
        return True, quality

    depth = str(getattr(policy, "depth", "standard") or "standard")
    if depth == "direct":
        # Direct lookup asks the archive for a topic/entity itself. Once topical
        # candidates exist, a semantic query-refinement call is usually pure cost;
        # extraction/validation can still conclude true insufficiency safely.
        if tuple(getattr(report, "candidates", ()) or ()):
            return False, "direct_policy_candidate_coverage"
        return True, "direct_policy_no_candidates"

    if depth == "deep":
        total = int(getattr(report, "required_facet_groups_total", 0) or 0)
        hit = int(getattr(report, "max_required_facet_groups_hit", 0) or 0)
        if total and hit < total:
            return True, "deep_plan_facet_incomplete"

    return bool(legacy_needs_refinement), str(legacy_reason)


def bounded_refinement_families(plan, families: Sequence) -> tuple:
    policy = getattr(plan, "retrieval_policy", None)
    if not bool(getattr(policy, "rescue_allowed", True)):
        return ()
    limit = int(getattr(policy, "max_rescue_families", 4) or 0)
    return tuple(families)[: max(0, min(limit, 4))]


def assess_integrated_answerability(pack, plan, report) -> AnswerabilityAssessment:
    """Prefer discussion-level co-location signals, retain legacy fallback.

    This decides whether extraction deserves a chance; it never creates facts.
    Every displayed claim is still exact-support validated and, when needed,
    semantically verified against only its admitted archive quotes.
    """
    base = assess_answerability(pack, plan, report)
    if not pack.messages:
        return base

    quality = str(getattr(report, "quality_state", "") or "")
    total = int(getattr(report, "required_facet_groups_total", 0) or 0)
    hit = int(getattr(report, "max_required_facet_groups_hit", 0) or 0)
    topic_discussions = int(getattr(report, "topic_anchored_discussions", 0) or 0)
    discussion_count = int(getattr(report, "discussion_count", 0) or 0)
    hydrated = int(getattr(report, "hydrated_discussions", 0) or 0)
    bridges = int(getattr(report, "topic_anchored_bridges", 0) or 0)

    topic_anchored = topic_discussions > 0 or base.topic_anchored
    facet_coverage = min(1.0, hit / total) if total else base.facet_coverage
    coherence = base.discussion_coherence or bool(discussion_count and (hydrated or bridges))

    if quality == "no_candidates":
        answerable, reason = False, "no_candidates"
    elif quality == "generic_noisy_coverage":
        answerable, reason = False, "topic_found_facet_missing"
    elif not topic_anchored:
        answerable, reason = False, "topic_found_facet_missing"
    elif total and hit < total:
        answerable, reason = False, "topic_found_facet_missing"
    elif quality in _COMPLETE_STATES:
        answerable, reason = True, "fragmented_but_answerable"
    elif quality == "only_topical_facet_missing":
        answerable, reason = False, "topic_found_facet_missing"
    else:
        answerable, reason = base.answerable, base.reason_code

    return replace(
        base,
        answerable=answerable,
        reason_code=reason,
        topic_anchored=topic_anchored,
        facet_coverage=facet_coverage,
        discussion_coherence=coherence,
    )


__all__ = [
    "INTEGRATION_POLICY_VERSION",
    "assess_integrated_answerability",
    "bounded_refinement_families",
    "should_refine_retrieval",
]
