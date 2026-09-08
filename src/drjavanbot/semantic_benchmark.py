from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import statistics
import tempfile
import time
from typing import Literal, Sequence

from drjavanbot.normalization import normalize_text
from drjavanbot.search import SearchQuery, SQLiteSearchBackend
from drjavanbot.storage import full_reindex
from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.retrieval import retrieve_with_plan

Expectation = Literal["present", "absent", "observe"]


@dataclass(frozen=True, slots=True)
class SemanticEvalCase:
    name: str
    question: str
    plan: SearchPlan
    anchors: tuple[str, ...]
    expectation: Expectation = "observe"
    # A strict faceted case must not pass merely because the topic is in one
    # result and a generic age/number word exists somewhere else in top-k. Every
    # required group must be present inside the SAME hydrated discussion bundle.
    required_anchor_groups: tuple[tuple[str, ...], ...] = ()
    min_relevant_top_k: int = 1


@dataclass(frozen=True, slots=True)
class SemanticCaseReport:
    name: str
    question: str
    expectation: Expectation
    latency_ms: float
    query_runs: int
    duplicate_queries_skipped: int
    families_with_hits: int
    context_hydrated: int
    discussion_windows: int
    conversation_bridges: int
    results: int
    top_k: int
    proxy_relevant_top_k: int
    top_k_relevance_proxy: float | None
    irrelevant_candidate_rate_proxy: float | None
    first_relevant_discussion_rank: int | None
    reciprocal_rank_proxy: float | None
    bounded_anchor_pool: int
    anchor_recall_proxy: float | None
    bounded_anchor_discussion_pool: int
    discussion_recall_proxy: float | None
    context_only_relevant_hits: int
    required_anchor_groups_hit: int
    required_anchor_groups_total: int
    max_colocated_required_groups: int
    colocated_required_groups_complete: bool
    independent_authors_top_k: int
    independent_threads_top_k: int
    discussion_count: int
    facet_complete_discussions: int
    topic_anchored_discussions: int
    hydrated_discussions: int
    duplicates_suppressed: int
    retrieval_quality_state: str
    top_score: float | None
    gate_passed: bool
    gate_reason: str


@dataclass(frozen=True, slots=True)
class SemanticBenchmarkReport:
    index_seconds: float
    db_size_bytes: int
    message_count: int
    archive_files: int
    top_k: int
    median_retrieval_ms: float | None
    p95_retrieval_ms: float | None
    cases: tuple[SemanticCaseReport, ...]
    strict_failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.strict_failures

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def default_semantic_cases() -> tuple[SemanticEvalCase, ...]:
    return (
        SemanticEvalCase(
            name="composite_recommendation",
            question="کدوم برند کامپوزیت خوبه؟",
            plan=_plan(
                "recommendation_comparison",
                ("کامپوزیت", "composite"),
                (
                    SearchFamily("topic", ("کامپوزیت", "composite", "رزین کامپوزیت")),
                    SearchFamily("experience", ("کامپوزیت تجربه", "composite experience")),
                    SearchFamily("comparison", ("کامپوزیت پیشنهاد", "کامپوزیت مقایسه")),
                ),
                entity_types=("product_or_brand",),
                required_aspects=("topic", "recommendation"),
            ),
            anchors=("کامپوزیت", "composite"),
            expectation="present",
        ),
        SemanticEvalCase(
            name="pediatric_orthodontic_timing",
            question="در بچه‌ها ارتودنسی رو در چه سنی باید استفاده کرد؟",
            plan=_plan(
                "timing_age",
                ("ارتودنسی", "orthodontic"),
                (
                    SearchFamily("topic", ("ارتودنسی", "orthodontic", "ortho")),
                    SearchFamily("population", ("کودک", "بچه", "اطفال", "pediatric")),
                    SearchFamily("timing", ("سن", "سالگی", "زمان شروع", "age")),
                    SearchFamily("topic_timing", ("سن ارتودنسی", "شروع ارتودنسی", "ارتودنسی کودک")),
                    # Search-only domain hints. They can locate a differently
                    # worded discussion but never become answer facts by themselves.
                    SearchFamily("domain_stage", ("دندان مختلط", "mixed dentition", "interceptive orthodontics", "فاز اول")),
                ),
                entity_types=("procedure",),
                required_aspects=("topic", "timing_age", "pediatric_population"),
            ),
            anchors=("ارتودنسی", "orthodont", "ortho"),
            required_anchor_groups=(
                ("ارتودنسی", "orthodont", "ortho"),
                ("سن", "سالگی", "سال", "age", "year"),
            ),
            min_relevant_top_k=4,
            expectation="present",
        ),
        SemanticEvalCase(
            name="direct_product_emax",
            question="e.max",
            plan=_plan(
                "direct_product_lookup",
                ("e max", "emax", "ایمکس"),
                (SearchFamily("product", ("e max", "emax", "ایمکس")),),
                entity_types=("product_or_material",),
            ),
            anchors=("e max", "emax", "ایمکس"),
            expectation="present",
        ),
        SemanticEvalCase(
            name="persian_question_english_product",
            question="برای e.max چه تجربه‌ای در گروه هست؟",
            plan=_plan(
                "experience_lookup",
                ("e max", "emax", "ایمکس"),
                (
                    SearchFamily("product", ("e max", "emax", "ایمکس")),
                    SearchFamily("experience", ("e max تجربه", "ایمکس تجربه")),
                ),
                entity_types=("product_or_material",),
            ),
            anchors=("e max", "emax", "ایمکس"),
            expectation="present",
        ),
        SemanticEvalCase(
            name="typo_zirconia",
            question="زیرکونیاا",
            plan=_plan(
                "typo_lookup",
                ("زیرکونیاا",),
                (SearchFamily("typo", ("زیرکونیاا",)),),
            ),
            anchors=("زیرکونیا",),
            expectation="observe",
        ),
        SemanticEvalCase(
            name="comparison_emax_zirconia",
            question="e.max یا زیرکونیا؟",
            plan=_plan(
                "comparison",
                ("e max", "زیرکونیا"),
                (
                    SearchFamily("emax", ("e max", "ایمکس")),
                    SearchFamily("zirconia", ("زیرکونیا", "zirconia")),
                    SearchFamily("comparison", ("e max زیرکونیا", "ایمکس زیرکونیا")),
                ),
                required_aspects=("topic", "comparison"),
            ),
            anchors=("e max", "ایمکس", "زیرکونیا", "zirconia"),
            expectation="observe",
        ),
        SemanticEvalCase(
            name="short_rct",
            question="RCT؟",
            plan=_plan(
                "short_lookup",
                ("rct", "درمان ریشه", "root canal"),
                (SearchFamily("topic", ("RCT", "درمان ریشه", "root canal")),),
            ),
            anchors=("rct", "درمان ریشه", "root canal"),
            expectation="present",
        ),
        SemanticEvalCase(
            name="generic_facet_pollution_guard",
            question="سن topic-sentinel-zzqv-generic چقدره؟",
            plan=_plan(
                "timing_age",
                ("topic-sentinel-zzqv-generic",),
                (
                    SearchFamily("topic", ("topic-sentinel-zzqv-generic",)),
                    SearchFamily("timing", ("سن", "سالگی", "age")),
                ),
                required_aspects=("topic", "timing_age"),
            ),
            anchors=("topic-sentinel-zzqv-generic",),
            expectation="absent",
        ),
        SemanticEvalCase(
            name="low_information_noise",
            question="چرا ریدی؟",
            plan=SearchPlan(
                searchable=False,
                intent="non_searchable",
                core_concepts=(),
                aliases=(),
                optional_concepts=(),
                entity_types=(),
                query_families=(),
                phrases=(),
                exclude_terms=(),
                low_information_terms=("چرا",),
                reply_context=False,
            ),
            anchors=(),
            expectation="absent",
        ),
        SemanticEvalCase(
            name="no_evidence_sentinel",
            question="zzqv9f7b6a21drjx",
            plan=_plan(
                "archive_lookup",
                ("zzqv9f7b6a21drjx",),
                (SearchFamily("sentinel", ("zzqv9f7b6a21drjx",)),),
                reply_context=False,
            ),
            anchors=("zzqv9f7b6a21drjx",),
            expectation="absent",
        ),
    )


def semantic_benchmark_archive(
    archive_dir: Path,
    *,
    cases: Sequence[SemanticEvalCase] | None = None,
    top_k: int = 12,
) -> SemanticBenchmarkReport:
    selected_cases = tuple(cases or default_semantic_cases())
    bounded_top_k = max(1, min(int(top_k), 40))
    with tempfile.TemporaryDirectory(prefix="drjavan-semantic-benchmark-") as temp:
        db_path = Path(temp) / "archive.sqlite3"
        index_report = full_reindex(archive_dir, db_path)
        backend = SQLiteSearchBackend(db_path)
        reports = tuple(_evaluate_case(backend, case, top_k=bounded_top_k) for case in selected_cases)
        failures = tuple(report.name for report in reports if not report.gate_passed)
        latencies = tuple(report.latency_ms for report in reports)
        median = statistics.median(latencies) if latencies else None
        p95 = _percentile_nearest_rank(latencies, 0.95)
        return SemanticBenchmarkReport(
            index_seconds=index_report.elapsed_seconds,
            db_size_bytes=index_report.db_size_bytes,
            message_count=index_report.messages,
            archive_files=index_report.archive_files,
            top_k=bounded_top_k,
            median_retrieval_ms=median,
            p95_retrieval_ms=p95,
            cases=reports,
            strict_failures=failures,
        )


def _evaluate_case(backend: SQLiteSearchBackend, case: SemanticEvalCase, *, top_k: int) -> SemanticCaseReport:
    started = time.perf_counter()
    retrieval = retrieve_with_plan(backend, case.plan, evidence_limit=max(top_k, 24))
    latency_ms = (time.perf_counter() - started) * 1000
    candidates = retrieval.candidates
    top = candidates[:top_k]

    anchors = tuple(normalize_text(anchor) for anchor in case.anchors if normalize_text(anchor))
    required_groups = tuple(
        tuple(normalize_text(anchor) for anchor in group if normalize_text(anchor))
        for group in case.required_anchor_groups
    )
    relevant = 0
    context_only = 0
    first_relevant_rank: int | None = None
    combined_texts: list[str] = []
    max_colocated_groups = 0
    for rank, candidate in enumerate(top, start=1):
        direct = normalize_text(candidate.message.text_normalized or candidate.message.text_raw)
        context = " ".join(normalize_text(item.text_normalized or item.text_raw) for item in candidate.context)
        bundle = " ".join((direct, context))
        combined_texts.extend((direct, context))
        direct_hit = any(anchor in direct for anchor in anchors)
        context_hit = any(anchor in context for anchor in anchors)
        if direct_hit or context_hit:
            relevant += 1
            if first_relevant_rank is None:
                first_relevant_rank = rank
        if not direct_hit and context_hit:
            context_only += 1
        if required_groups:
            bundle_groups = sum(
                1 for group in required_groups
                if group and any(anchor in bundle for anchor in group)
            )
            max_colocated_groups = max(max_colocated_groups, bundle_groups)

    corpus_view = " ".join(combined_texts)
    groups_hit = sum(
        1 for group in required_groups
        if group and any(anchor in corpus_view for anchor in group)
    )
    colocated_complete = bool(required_groups) and max_colocated_groups == len(required_groups)

    relevance_proxy = (relevant / len(top)) if top and anchors else None
    irrelevant_proxy = (1.0 - relevance_proxy) if relevance_proxy is not None else None
    reciprocal_rank = (1.0 / first_relevant_rank) if first_relevant_rank is not None else None

    anchor_pool: set[str] = set()
    anchor_discussion_pool: set[tuple[int, int]] = set()
    for anchor in case.anchors[:8]:
        for item in backend.search(SearchQuery(
            raw_query=anchor,
            evidence_limit=100,
            candidate_limit=160,
            include_context=False,
        )):
            anchor_pool.add(item.message.source_locator)
            anchor_discussion_pool.add(_discussion_proxy_key(item.message))
    selected_refs = {item.message.source_locator for item in top}
    recall_proxy = (len(selected_refs & anchor_pool) / len(anchor_pool)) if anchor_pool else None
    selected_discussions = {_candidate_discussion_proxy_key(item) for item in top}
    discussion_recall = (
        len(selected_discussions & anchor_discussion_pool) / len(anchor_discussion_pool)
        if anchor_discussion_pool else None
    )

    authors = {
        item.message.author_normalized or item.message.author
        for item in top
        if item.message.author_normalized or item.message.author
    }
    threads = {
        item.cluster_key or f"window:{item.message.source_page}:{item.message.source_order // 8}"
        for item in top
    }
    gate_passed, gate_reason = _gate(
        case,
        result_count=len(candidates),
        relevant_top_k=relevant,
        required_groups_hit=groups_hit,
        required_groups_total=len(required_groups),
        max_colocated_required_groups=max_colocated_groups,
    )
    return SemanticCaseReport(
        name=case.name,
        question=case.question,
        expectation=case.expectation,
        latency_ms=round(latency_ms, 3),
        query_runs=retrieval.query_runs,
        duplicate_queries_skipped=retrieval.duplicate_queries_skipped,
        families_with_hits=retrieval.families_with_hits,
        context_hydrated=retrieval.context_hydrated,
        discussion_windows=retrieval.discussion_windows,
        conversation_bridges=retrieval.conversation_bridges,
        results=len(candidates),
        top_k=len(top),
        proxy_relevant_top_k=relevant,
        top_k_relevance_proxy=round(relevance_proxy, 4) if relevance_proxy is not None else None,
        irrelevant_candidate_rate_proxy=round(irrelevant_proxy, 4) if irrelevant_proxy is not None else None,
        first_relevant_discussion_rank=first_relevant_rank,
        reciprocal_rank_proxy=round(reciprocal_rank, 4) if reciprocal_rank is not None else None,
        bounded_anchor_pool=len(anchor_pool),
        anchor_recall_proxy=round(recall_proxy, 4) if recall_proxy is not None else None,
        bounded_anchor_discussion_pool=len(anchor_discussion_pool),
        discussion_recall_proxy=round(discussion_recall, 4) if discussion_recall is not None else None,
        context_only_relevant_hits=context_only,
        required_anchor_groups_hit=groups_hit,
        required_anchor_groups_total=len(required_groups),
        max_colocated_required_groups=max_colocated_groups,
        colocated_required_groups_complete=colocated_complete,
        independent_authors_top_k=len(authors),
        independent_threads_top_k=len(threads),
        discussion_count=retrieval.discussion_count,
        facet_complete_discussions=retrieval.facet_complete_discussions,
        topic_anchored_discussions=retrieval.topic_anchored_discussions,
        hydrated_discussions=retrieval.hydrated_discussions,
        duplicates_suppressed=retrieval.duplicates_suppressed,
        retrieval_quality_state=retrieval.quality_state,
        top_score=round(top[0].local_score, 6) if top else None,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
    )


def _gate(
    case: SemanticEvalCase,
    *,
    result_count: int,
    relevant_top_k: int,
    required_groups_hit: int,
    required_groups_total: int,
    max_colocated_required_groups: int,
) -> tuple[bool, str]:
    if case.expectation == "absent":
        return (result_count == 0, "expected no retrieval candidates")
    if case.expectation == "present":
        if result_count == 0:
            return False, "expected archive candidates but none were retrieved"
        if case.anchors and relevant_top_k < max(1, case.min_relevant_top_k):
            return False, f"topical retrieval too weak ({relevant_top_k}/{case.min_relevant_top_k})"
        if required_groups_total and required_groups_hit < required_groups_total:
            return False, f"required answer facets missing globally ({required_groups_hit}/{required_groups_total})"
        if required_groups_total and max_colocated_required_groups < required_groups_total:
            return False, (
                "required answer facets were not found in one discussion bundle "
                f"({max_colocated_required_groups}/{required_groups_total})"
            )
        return True, "expected archive topic and colocated required facets retrieved"
    return True, "observational metric only"


def _discussion_proxy_key(message) -> tuple[int, int]:
    return (int(message.source_page), int(message.source_order) // 8)


def _candidate_discussion_proxy_key(candidate) -> tuple[int, int]:
    return _discussion_proxy_key(candidate.message)


def _percentile_nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(max(0.0, min(1.0, percentile)) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _plan(
    intent: str,
    concepts: tuple[str, ...],
    families: tuple[SearchFamily, ...],
    *,
    entity_types: tuple[str, ...] = (),
    reply_context: bool = True,
    required_aspects: tuple[str, ...] = (),
) -> SearchPlan:
    return SearchPlan(
        searchable=True,
        intent=intent,
        core_concepts=concepts,
        aliases=(),
        optional_concepts=(),
        entity_types=entity_types,
        query_families=families,
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=reply_context,
        required_aspects=required_aspects,
    )


__all__ = [
    "SemanticBenchmarkReport",
    "SemanticCaseReport",
    "SemanticEvalCase",
    "default_semantic_cases",
    "semantic_benchmark_archive",
]
