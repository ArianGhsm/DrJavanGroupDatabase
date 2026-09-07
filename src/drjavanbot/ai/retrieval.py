from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Sequence

from drjavanbot.normalization import normalize_text, tokenize
from drjavanbot.search import EvidenceCandidate, SearchBackend, SearchQuery
from drjavanbot.search.terms import informative_query
from .planner import SearchPlan


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    candidates: tuple[EvidenceCandidate, ...]
    query_runs: int
    families_with_hits: int
    duplicate_queries_skipped: int = 0
    context_hydrated: int = 0
    discussion_windows: int = 0
    conversation_bridges: int = 0
    hit_family_names: tuple[str, ...] = ()


def retrieve_with_plan(
    backend: SearchBackend,
    plan: SearchPlan,
    *,
    candidate_limit: int = 160,
    evidence_limit: int = 56,
) -> RetrievalReport:
    """Run faceted lexical families, bridge topic-anchored nearby hits, hydrate context."""
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
                candidate_limit=bounded_candidate_limit,
                evidence_limit=per_family_evidence_limit,
                reply_depth=5 if plan.reply_context else 1,
                context_before=None if plan.reply_context else 0,
                context_after=None if plan.reply_context else 0,
                include_context=False,
            ),
        ))

    if not prepared:
        return RetrievalReport((), 0, 0, duplicate_queries_skipped, 0, 0, 0, ())

    queries = tuple(query for _, query in prepared)
    batch_search = getattr(backend, "search_many", None)
    if callable(batch_search):
        result_sets = tuple(tuple(values) for values in batch_search(queries))
        if len(result_sets) != len(queries):
            result_sets = tuple(tuple(backend.search(query)) for query in queries)
    else:
        result_sets = tuple(tuple(backend.search(query)) for query in queries)

    runs: list[tuple[str, tuple[EvidenceCandidate, ...]]] = []
    hit_families: set[str] = set()
    for (family_name, _), result in zip(prepared, result_sets):
        runs.append((family_name, result))
        if result:
            hit_families.add(family_name)

    anchor_families = _anchor_family_names(plan)
    fused, bridge_count = _fuse_runs(runs, limit=evidence_limit, anchor_families=anchor_families)
    hydrated, hydrated_count, discussion_windows = _hydrate_context_after_fusion(
        backend,
        fused,
        reply_context=plan.reply_context,
        reply_depth=5,
        limit=min(max(12, evidence_limit // 2), 20),
    )
    return RetrievalReport(
        candidates=hydrated,
        query_runs=len(runs),
        families_with_hits=len(hit_families),
        duplicate_queries_skipped=duplicate_queries_skipped,
        context_hydrated=hydrated_count,
        discussion_windows=discussion_windows,
        conversation_bridges=bridge_count,
        hit_family_names=tuple(sorted(hit_families)),
    )


def assess_planned_retrieval(report: RetrievalReport) -> tuple[bool, str]:
    candidates = report.candidates
    if not candidates:
        return True, "no_candidates"
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
    anchored_bridges = sum(
        1 for c in candidates[:12]
        if "conversation_bridge" in c.match_reasons and "anchor_family_hit" in c.match_reasons
    )
    if anchored_bridges >= 2 and len(authors) >= 2:
        return False, "topic_anchored_conversation_coverage"
    if len(candidates) >= 2 and len(authors) >= 2 and strong_reasons >= 2 and (
        report.families_with_hits >= 2 or max_family_coverage >= 2
    ):
        return False, "strong_multi_family_match"
    if len(candidates) >= 5 and len(authors) >= 2 and (
        report.families_with_hits >= 2 or max_family_coverage >= 2 or strong_reasons >= 3
    ):
        return False, "multi_source_coverage"
    if candidates[0].local_score >= 7.0 and len(authors) >= 2 and strong_reasons >= 2:
        return False, "strong_topical_match"
    return True, "coverage_or_diversity_weak"


def _hydrate_context_after_fusion(
    backend: SearchBackend,
    candidates: Sequence[EvidenceCandidate],
    *,
    reply_context: bool,
    reply_depth: int,
    limit: int,
) -> tuple[tuple[EvidenceCandidate, ...], int, int]:
    values = tuple(candidates)
    if not values or not reply_context:
        return values, 0, 0

    bounded_limit = min(max(0, int(limit)), len(values))
    anchors = values[:bounded_limit]
    output: list[EvidenceCandidate] = []
    hydrated_count = 0
    discussion_windows = 0

    for index, candidate in enumerate(values):
        if index >= bounded_limit or candidate.context:
            output.append(candidate)
            continue

        before, after, discussion = _discussion_window(candidate, anchors)
        try:
            context = tuple(
                backend.get_context(
                    candidate.message,
                    before=before,
                    after=after,
                    follow_reply=True,
                )
            )
        except Exception:
            context = ()

        if not context:
            output.append(candidate)
            continue

        hydrated_count += 1
        if discussion:
            discussion_windows += 1
        reasons = set(candidate.match_reasons)
        reasons.add("context_available")
        if discussion:
            reasons.add("discussion_window")
        score = candidate.local_score + 0.20 + (0.16 if discussion else 0.0)
        output.append(replace(
            candidate,
            local_score=round(score, 6),
            match_reasons=tuple(sorted(reasons)),
            context=context,
        ))

    return tuple(output), hydrated_count, discussion_windows


def _discussion_window(
    candidate: EvidenceCandidate,
    anchors: Sequence[EvidenceCandidate],
) -> tuple[int, int, bool]:
    message = candidate.message
    normalized = normalize_text(message.text_normalized or message.text_raw)
    token_count = len(tokenize(normalized))
    short_or_context_dependent = len(normalized) <= 56 or token_count <= 6

    bridge_coverage = _bridge_coverage(candidate.match_reasons)
    if "conversation_bridge" in candidate.match_reasons:
        if "anchor_family_hit" in candidate.match_reasons and bridge_coverage >= 3:
            return 6, 8, True
        if "anchor_family_hit" in candidate.match_reasons:
            return 5, 7, True
        # A facet-only hit near a topic anchor is useful mainly as context. Keep
        # its own expansion bounded because the topic anchor receives the wider
        # window and will normally pull this short reply in.
        return 3, 4, True

    nearby_hits = 0
    for other in anchors:
        if other.message.source_locator == message.source_locator:
            continue
        if other.message.source_page != message.source_page:
            continue
        if abs(other.message.source_order - message.source_order) <= 12:
            nearby_hits += 1

    if nearby_hits >= 2:
        return 4, 6, True
    if nearby_hits == 1:
        return 3, 5, True
    if message.reply_to_message_id is not None or short_or_context_dependent:
        return 3, 4, True
    return 2, 3, False


def _fuse_runs(
    runs: Sequence[tuple[str, Sequence[EvidenceCandidate]]],
    *,
    limit: int,
    anchor_families: set[str] | None = None,
) -> tuple[tuple[EvidenceCandidate, ...], int]:
    anchors = set(anchor_families or ())
    state: dict[str, dict[str, object]] = {}
    positional_hits: list[tuple[str, int, int, str]] = []

    for family, candidates in runs:
        for rank, candidate in enumerate(candidates, start=1):
            key = candidate.message.source_locator
            positional_hits.append((family, candidate.message.source_page, candidate.message.source_order, key))
            item = state.setdefault(
                key,
                {
                    "candidate": candidate,
                    "score": 0.0,
                    "families": set(),
                    "terms": set(),
                    "reasons": set(),
                    "best_local": candidate.local_score,
                },
            )
            families: set[str] = item["families"]  # type: ignore[assignment]
            terms: set[str] = item["terms"]  # type: ignore[assignment]
            reasons: set[str] = item["reasons"]  # type: ignore[assignment]
            score = float(item["score"])
            score += min(max(candidate.local_score, 0.0), 12.0) * 0.34
            score += 10.0 / (50.0 + rank)
            item["score"] = score
            item["best_local"] = max(float(item["best_local"]), candidate.local_score)
            families.add(family)
            terms.update(candidate.matched_terms)
            reasons.update(candidate.match_reasons)
            current: EvidenceCandidate = item["candidate"]  # type: ignore[assignment]
            if len(candidate.context) > len(current.context):
                item["candidate"] = candidate

    bridge_count = 0
    for key, item in state.items():
        candidate: EvidenceCandidate = item["candidate"]  # type: ignore[assignment]
        direct_families: set[str] = item["families"]  # type: ignore[assignment]
        direct_anchor = bool(direct_families & anchors) if anchors else True
        nearby_families = set(direct_families)
        for family, page, order, other_key in positional_hits:
            if other_key == key or page != candidate.message.source_page:
                continue
            if abs(order - candidate.message.source_order) <= 14:
                nearby_families.add(family)

        nearby_has_anchor = bool(nearby_families & anchors) if anchors else True
        has_new_family = len(nearby_families) > len(direct_families)
        if has_new_family and nearby_has_anchor:
            coverage = len(nearby_families)
            reasons: set[str] = item["reasons"]  # type: ignore[assignment]
            reasons.add("conversation_bridge")
            reasons.add(f"bridge_family_coverage:{coverage}")
            if direct_anchor:
                reasons.add("anchor_family_hit")
                bridge_bonus = min(1.9, 0.78 + 0.34 * max(0, coverage - 2))
            else:
                bridge_bonus = min(0.70, 0.32 + 0.16 * max(0, coverage - 2))
            item["score"] = float(item["score"]) + bridge_bonus
            bridge_count += 1

    fused: list[EvidenceCandidate] = []
    for item in state.values():
        candidate: EvidenceCandidate = item["candidate"]  # type: ignore[assignment]
        families: set[str] = item["families"]  # type: ignore[assignment]
        reasons: set[str] = item["reasons"]  # type: ignore[assignment]
        score = float(item["score"])
        direct_anchor = bool(families & anchors) if anchors else True
        if direct_anchor:
            score += 1.05
            reasons.add("anchor_family_hit")
        if len(families) > 1:
            score += min(2.0, 0.65 * (len(families) - 1))
        if candidate.message.reply_to_message_id is not None:
            score += 0.35
            reasons.add("reply_context")
        if candidate.context:
            score += 0.20
            reasons.add("context_available")
        if "exact_phrase" in reasons:
            score += 0.8
        elif "normalized_tokens" in reasons:
            score += 0.35
        reasons.add(f"family_coverage:{len(families)}")
        fused.append(
            replace(
                candidate,
                local_score=round(score, 6),
                matched_terms=tuple(sorted(item["terms"])),  # type: ignore[arg-type]
                match_reasons=tuple(sorted(reasons)),
            )
        )

    fused.sort(key=lambda c: (-c.local_score, c.message.source_page, c.message.source_order))
    selected: list[EvidenceCandidate] = []
    author_counts: dict[str, int] = defaultdict(int)
    neighborhood_counts: dict[tuple[int, int], int] = defaultdict(int)
    remaining = list(fused)
    while remaining and len(selected) < max(1, min(limit, 120)):
        best_index = 0
        best_effective = float("-inf")
        for index, candidate in enumerate(remaining[:120]):
            author = candidate.message.author_normalized or candidate.message.author or ""
            neighborhood = (candidate.message.source_page, candidate.message.source_order // 8)
            effective = candidate.local_score
            if "anchor_family_hit" in candidate.match_reasons:
                effective += 0.38
            effective -= min(0.9, author_counts[author] * 0.28) if author else 0.0
            neighborhood_penalty = min(0.8, neighborhood_counts[neighborhood] * 0.30)
            if "conversation_bridge" in candidate.match_reasons and "anchor_family_hit" in candidate.match_reasons:
                neighborhood_penalty *= 0.55
            effective -= neighborhood_penalty
            if effective > best_effective:
                best_effective = effective
                best_index = index
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        author = chosen.message.author_normalized or chosen.message.author or ""
        if author:
            author_counts[author] += 1
        neighborhood_counts[(chosen.message.source_page, chosen.message.source_order // 8)] += 1
    return tuple(selected), bridge_count


def _anchor_family_names(plan: SearchPlan) -> set[str]:
    """Families that directly carry the user's core clinical concept."""
    core = tuple(
        normalize_text(value)
        for value in (*plan.core_concepts, *plan.aliases)
        if normalize_text(value) and len(normalize_text(value)) >= 3
    )
    out: set[str] = set()
    for family in plan.query_families:
        name = family.name.casefold()
        if any(token in name for token in ("topic", "core", "procedure", "product", "material", "entity")):
            out.add(family.name)
            continue
        for query in family.queries:
            normalized = normalize_text(query)
            if any(term in normalized or normalized in term for term in core):
                out.add(family.name)
                break
    if not out and plan.query_families:
        # Fail soft: the first family is planner-defined and is generally the
        # main topic family. This affects ranking only, never evidence validity.
        out.add(plan.query_families[0].name)
    return out


def _family_coverage(reasons: Sequence[str]) -> int:
    for reason in reasons:
        if reason.startswith("family_coverage:"):
            try:
                return int(reason.split(":", 1)[1])
            except ValueError:
                return 0
    return 0


def _bridge_coverage(reasons: Sequence[str]) -> int:
    for reason in reasons:
        if reason.startswith("bridge_family_coverage:"):
            try:
                return int(reason.split(":", 1)[1])
            except ValueError:
                return 0
    return 0


__all__ = ["RetrievalReport", "assess_planned_retrieval", "retrieve_with_plan"]
