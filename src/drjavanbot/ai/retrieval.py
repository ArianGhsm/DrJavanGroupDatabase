from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Sequence

from drjavanbot.normalization import normalize_text
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


def retrieve_with_plan(
    backend: SearchBackend,
    plan: SearchPlan,
    *,
    candidate_limit: int = 96,
    evidence_limit: int = 36,
) -> RetrievalReport:
    """Run independent lexical families, fuse winners, then hydrate context.

    Search-plan hints determine *where to look* but never become evidence. Query
    de-duplication occurs after low-information filtering so semantically identical
    lexical queries cannot fake family coverage.

    Context is deliberately deferred until after fusion. The previous design
    expanded reply/neighborhood context for up to 24 candidates in every query
    family and then discarded most of that work during fusion. On the production
    249k-message archive this produced multi-second amplification. The optimized
    path retrieves cheap primary candidates first, fuses/diversifies them, and
    hydrates context only for the bounded fused winners.
    """
    prepared: list[tuple[str, SearchQuery]] = []
    seen_queries: set[str] = set()
    duplicate_queries_skipped = 0
    per_family_evidence_limit = max(8, min(20, evidence_limit))
    bounded_candidate_limit = max(20, min(candidate_limit, 140))

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
                reply_depth=4 if plan.reply_context else 1,
                context_before=None if plan.reply_context else 0,
                context_after=None if plan.reply_context else 0,
                include_context=False,
            ),
        ))

    if not prepared:
        return RetrievalReport((), 0, 0, duplicate_queries_skipped, 0)

    queries = tuple(query for _, query in prepared)
    batch_search = getattr(backend, "search_many", None)
    if callable(batch_search):
        result_sets = tuple(tuple(values) for values in batch_search(queries))
        if len(result_sets) != len(queries):
            # Capability implementations must preserve positional correspondence.
            # Fall back safely rather than silently mis-assigning query families.
            result_sets = tuple(tuple(backend.search(query)) for query in queries)
    else:
        result_sets = tuple(tuple(backend.search(query)) for query in queries)

    runs: list[tuple[str, tuple[EvidenceCandidate, ...]]] = []
    hit_families: set[str] = set()
    for (family_name, _), result in zip(prepared, result_sets):
        runs.append((family_name, result))
        if result:
            hit_families.add(family_name)

    fused = _fuse_runs(runs, limit=evidence_limit)
    hydrated, hydrated_count = _hydrate_context_after_fusion(
        backend,
        fused,
        reply_context=plan.reply_context,
        reply_depth=4,
        limit=min(max(8, evidence_limit), 24),
    )
    return RetrievalReport(
        candidates=hydrated,
        query_runs=len(runs),
        families_with_hits=len(hit_families),
        duplicate_queries_skipped=duplicate_queries_skipped,
        context_hydrated=hydrated_count,
    )


def assess_planned_retrieval(report: RetrievalReport) -> tuple[bool, str]:
    candidates = report.candidates
    if not candidates:
        return True, "no_candidates"
    authors = {
        c.message.author_normalized or c.message.author
        for c in candidates[:12]
        if c.message.author_normalized or c.message.author
    }
    strong_reasons = sum(
        1 for c in candidates[:8]
        if any(reason in {"exact_phrase", "normalized_tokens", "synonym"} for reason in c.match_reasons)
    )
    max_family_coverage = max((_family_coverage(c.match_reasons) for c in candidates[:8]), default=0)
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
) -> tuple[tuple[EvidenceCandidate, ...], int]:
    values = tuple(candidates)
    if not values or not reply_context:
        return values, 0

    bounded_limit = min(max(0, int(limit)), len(values))
    hydrate_many = getattr(backend, "hydrate_context", None)
    if callable(hydrate_many):
        hydrated = tuple(hydrate_many(values, reply_depth=reply_depth, limit=bounded_limit))
        if len(hydrated) == len(values):
            count = sum(1 for before, after in zip(values[:bounded_limit], hydrated[:bounded_limit]) if after.context and not before.context)
            return hydrated, count

    # Protocol-compatible fallback for custom/test backends. Existing context is
    # never replaced; only empty fused winners are hydrated.
    output: list[EvidenceCandidate] = []
    count = 0
    for index, candidate in enumerate(values):
        if index >= bounded_limit or candidate.context:
            output.append(candidate)
            continue
        try:
            context = tuple(backend.get_context(candidate.message, before=1, after=2, follow_reply=True))
        except Exception:
            context = ()
        if context:
            count += 1
            reasons = set(candidate.match_reasons)
            reasons.add("context_available")
            output.append(replace(
                candidate,
                local_score=round(candidate.local_score + 0.20, 6),
                match_reasons=tuple(sorted(reasons)),
                context=context,
            ))
        else:
            output.append(candidate)
    return tuple(output), count


def _fuse_runs(
    runs: Sequence[tuple[str, Sequence[EvidenceCandidate]]], *, limit: int
) -> tuple[EvidenceCandidate, ...]:
    state: dict[str, dict[str, object]] = {}
    for family, candidates in runs:
        for rank, candidate in enumerate(candidates, start=1):
            key = candidate.message.source_locator
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

    fused: list[EvidenceCandidate] = []
    for item in state.values():
        candidate: EvidenceCandidate = item["candidate"]  # type: ignore[assignment]
        families: set[str] = item["families"]  # type: ignore[assignment]
        reasons: set[str] = item["reasons"]  # type: ignore[assignment]
        score = float(item["score"])
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
    while remaining and len(selected) < max(1, min(limit, 100)):
        best_index = 0
        best_effective = float("-inf")
        for index, candidate in enumerate(remaining[:80]):
            author = candidate.message.author_normalized or candidate.message.author or ""
            neighborhood = (candidate.message.source_page, candidate.message.source_order // 6)
            effective = candidate.local_score
            effective -= min(0.9, author_counts[author] * 0.28) if author else 0.0
            effective -= min(0.8, neighborhood_counts[neighborhood] * 0.32)
            if effective > best_effective:
                best_effective = effective
                best_index = index
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        author = chosen.message.author_normalized or chosen.message.author or ""
        if author:
            author_counts[author] += 1
        neighborhood_counts[(chosen.message.source_page, chosen.message.source_order // 6)] += 1
    return tuple(selected)


def _family_coverage(reasons: Sequence[str]) -> int:
    for reason in reasons:
        if reason.startswith("family_coverage:"):
            try:
                return int(reason.split(":", 1)[1])
            except ValueError:
                return 0
    return 0


__all__ = ["RetrievalReport", "assess_planned_retrieval", "retrieve_with_plan"]
