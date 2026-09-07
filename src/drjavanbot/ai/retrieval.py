from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Sequence

from drjavanbot.search import EvidenceCandidate, SearchBackend, SearchQuery
from drjavanbot.search.terms import informative_query
from .planner import SearchPlan


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    candidates: tuple[EvidenceCandidate, ...]
    query_runs: int
    families_with_hits: int


def retrieve_with_plan(
    backend: SearchBackend,
    plan: SearchPlan,
    *,
    candidate_limit: int = 96,
    evidence_limit: int = 36,
) -> RetrievalReport:
    """Run independent semantic query families and fuse them deterministically.

    The planner proposes what to look for; the local archive remains the only
    source of evidence. No planner string can become evidence on its own.
    """
    runs: list[tuple[str, tuple[EvidenceCandidate, ...]]] = []
    hit_families: set[str] = set()
    for family_name, raw_query in plan.queries:
        query_text = informative_query(raw_query)
        if not query_text:
            continue
        result = tuple(
            backend.search(
                SearchQuery(
                    raw_query=query_text,
                    candidate_limit=max(20, min(candidate_limit, 160)),
                    evidence_limit=max(8, min(24, evidence_limit)),
                    reply_depth=4 if plan.reply_context else 1,
                    context_before=None if plan.reply_context else 0,
                    context_after=None if plan.reply_context else 0,
                )
            )
        )
        runs.append((family_name, result))
        if result:
            hit_families.add(family_name)
    return RetrievalReport(
        candidates=_fuse_runs(runs, limit=evidence_limit),
        query_runs=len(runs),
        families_with_hits=len(hit_families),
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
