from __future__ import annotations

from collections import Counter
from drjavanbot.normalization import normalize_text
from drjavanbot.search import SearchBackend, SearchQuery
from drjavanbot.search.terms import informative_query
from .planner import SearchPlan
from .discussion_types import DiscussionCandidate, MAX_DISCUSSIONS
from .discussion_query import _collect_message_states, _query_surface_variants
from .discussion_facets import _anchor_family_names, _mark_topic_anchors, _required_family_groups
from .discussion_cluster import _build_discussions
from .discussion_hydrate import _hydrate_top_discussions
from .discussion_select import _extract_evidence_candidates, _quality_state
from .discussion_features import _family_coverage
from .retrieval_contracts import RetrievalReport

def retrieve_with_plan(
    backend: SearchBackend,
    plan: SearchPlan,
    *,
    candidate_limit: int = 160,
    evidence_limit: int = 56,
) -> RetrievalReport:
    """Retrieve bounded topic-anchored discussions, then hydrate only their winners."""
    prepared: list[tuple[str, SearchQuery]] = []
    seen_queries: set[str] = set()
    duplicate_queries_skipped = 0
    per_family_evidence_limit = max(10, min(24, evidence_limit))
    bounded_candidate_limit = max(40, min(candidate_limit, 220))

    for family_name, raw_query in plan.queries:
        query_text = informative_query(raw_query)
        query_key = normalize_text(query_text)
        if not query_key:
            continue
        if query_key in seen_queries:
            duplicate_queries_skipped += 1
            continue
        seen_queries.add(query_key)
        prepared.append((
            family_name,
            SearchQuery(
                raw_query=query_text,
                variants=_query_surface_variants(query_text),
                candidate_limit=bounded_candidate_limit,
                evidence_limit=per_family_evidence_limit,
                reply_depth=5 if plan.reply_context else 1,
                context_before=None if plan.reply_context else 0,
                context_after=None if plan.reply_context else 0,
                include_context=False,
            ),
        ))

    if not prepared:
        return RetrievalReport(
            candidates=(),
            query_runs=0,
            families_with_hits=0,
            duplicate_queries_skipped=duplicate_queries_skipped,
        )

    queries = tuple(query for _, query in prepared)
    batch_search = getattr(backend, "search_many", None)
    if callable(batch_search):
        result_sets = tuple(tuple(values) for values in batch_search(queries))
        if len(result_sets) != len(queries):
            result_sets = tuple(tuple(backend.search(query)) for query in queries)
    else:
        result_sets = tuple(tuple(backend.search(query)) for query in queries)

    runs = []
    hit_families: set[str] = set()
    raw_hit_count = 0
    for (family_name, search_query), result in zip(prepared, result_sets):
        runs.append((family_name, search_query.raw_query, result))
        raw_hit_count += len(result)
        if result:
            hit_families.add(family_name)

    anchor_families = _anchor_family_names(plan)
    required_groups = _required_family_groups(plan, anchor_families)
    states = _collect_message_states(runs)
    _mark_topic_anchors(states, plan=plan, anchor_families=anchor_families)
    duplicates_suppressed = max(0, raw_hit_count - len(states))

    discussions, bridge_count = _build_discussions(
        states,
        anchor_families=anchor_families,
        required_groups=required_groups,
    )

    # A multi-family plan with no topic-anchor hit must not promote generic facet
    # matches (for example bare age/year hits) into evidence.
    has_topic_anchor = any(discussion.topic_anchored for discussion in discussions)
    has_non_anchor_families = bool(hit_families - anchor_families)
    if anchor_families and has_non_anchor_families and not has_topic_anchor:
        discussions = ()

    ranked = tuple(sorted(
        discussions,
        key=lambda item: (
            -item.score,
            item.representative.message.source_page,
            item.representative.message.source_order,
        ),
    )[:MAX_DISCUSSIONS])

    hydration_limit = min(12, max(6, max(1, evidence_limit) // 4))
    hydrated, hydrated_count, discussion_windows = _hydrate_top_discussions(
        backend,
        ranked,
        reply_context=plan.reply_context,
        reply_depth=5,
        limit=hydration_limit,
    )
    candidates = _extract_evidence_candidates(hydrated, limit=evidence_limit)

    facet_complete = sum(1 for item in hydrated if item.facet_complete)
    reason_counts = Counter(
        reason
        for item in hydrated[:24]
        for reason in item.ranking_reasons
        if not reason.startswith("discussion_span:")
    )
    quality_state = _quality_state(
        plan,
        hydrated,
        candidates,
        required_groups=required_groups,
    )
    return RetrievalReport(
        candidates=candidates,
        query_runs=len(runs),
        families_with_hits=len(hit_families),
        duplicate_queries_skipped=duplicate_queries_skipped,
        context_hydrated=hydrated_count,
        discussion_windows=discussion_windows,
        conversation_bridges=bridge_count,
        hit_family_names=tuple(sorted(hit_families)),
        discussion_count=len(hydrated),
        facet_complete_discussions=facet_complete,
        topic_anchored_bridges=bridge_count,
        hydrated_discussions=hydrated_count,
        duplicates_suppressed=duplicates_suppressed,
        ranking_reason_counts=tuple(sorted(reason_counts.items()))[:24],
        quality_state=quality_state,
        families_executed=len({family for family, _query in prepared}),
        topic_anchored_discussions=sum(1 for item in hydrated if item.topic_anchored),
        required_facet_groups_total=len(required_groups),
        max_required_facet_groups_hit=max(
            (item.required_facet_groups_hit for item in hydrated),
            default=0,
        ),
    )


def assess_planned_retrieval(report: RetrievalReport) -> tuple[bool, str]:
    """Return whether a planner/refinement rescue is still useful."""
    if not report.candidates:
        return True, "no_candidates"
    if report.quality_state == "facet_complete_discussion":
        return False, "facet_complete_discussion"
    if report.quality_state == "strong_direct_answer_candidate":
        return False, "strong_direct_answer_candidate"
    if report.quality_state == "only_topical_facet_missing":
        return True, "required_facet_missing"
    if report.quality_state == "generic_noisy_coverage":
        return True, "generic_noisy_coverage"

    candidates = report.candidates
    authors = {
        c.message.author_normalized or c.message.author
        for c in candidates[:16]
        if c.message.author_normalized or c.message.author
    }
    strong_reasons = sum(
        1 for c in candidates[:10]
        if any(reason in {"exact_phrase", "normalized_tokens", "synonym"} for reason in c.match_reasons)
    )
    max_family_coverage = max((_family_coverage(c.match_reasons) for c in candidates[:10]), default=0)
    if (
        len(candidates) >= 3
        and len(authors) >= 2
        and strong_reasons >= 2
        and (report.families_with_hits >= 2 or max_family_coverage >= 2)
    ):
        return False, "multi_discussion_coverage"
    return True, "coverage_or_diversity_weak"


__all__ = ["DiscussionCandidate", "RetrievalReport", "assess_planned_retrieval", "retrieve_with_plan"]
