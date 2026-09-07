from __future__ import annotations

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True, slots=True)
class SemanticCaseReport:
    name: str
    question: str
    expectation: Expectation
    latency_ms: float
    query_runs: int
    duplicate_queries_skipped: int
    families_with_hits: int
    results: int
    top_k: int
    proxy_relevant_top_k: int
    top_k_relevance_proxy: float | None
    irrelevant_candidate_rate_proxy: float | None
    bounded_anchor_pool: int
    anchor_recall_proxy: float | None
    context_only_relevant_hits: int
    independent_authors_top_k: int
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
            ),
            anchors=("کامپوزیت", "composite"),
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
        median = statistics.median(report.latency_ms for report in reports) if reports else None
        return SemanticBenchmarkReport(
            index_seconds=index_report.elapsed_seconds,
            db_size_bytes=index_report.db_size_bytes,
            message_count=index_report.messages,
            archive_files=index_report.archive_files,
            top_k=bounded_top_k,
            median_retrieval_ms=median,
            cases=reports,
            strict_failures=failures,
        )


def _evaluate_case(backend: SQLiteSearchBackend, case: SemanticEvalCase, *, top_k: int) -> SemanticCaseReport:
    started = time.perf_counter()
    retrieval = retrieve_with_plan(backend, case.plan, evidence_limit=max(top_k, 20))
    latency_ms = (time.perf_counter() - started) * 1000
    candidates = retrieval.candidates
    top = candidates[:top_k]

    anchors = tuple(normalize_text(anchor) for anchor in case.anchors if normalize_text(anchor))
    relevant = 0
    context_only = 0
    for candidate in top:
        direct = normalize_text(candidate.message.text_normalized or candidate.message.text_raw)
        context = " ".join(normalize_text(item.text_normalized or item.text_raw) for item in candidate.context)
        direct_hit = any(anchor in direct for anchor in anchors)
        context_hit = any(anchor in context for anchor in anchors)
        if direct_hit or context_hit:
            relevant += 1
        if not direct_hit and context_hit:
            context_only += 1

    relevance_proxy = (relevant / len(top)) if top and anchors else None
    irrelevant_proxy = (1.0 - relevance_proxy) if relevance_proxy is not None else None

    # Recall proxy only needs the matching message locator. Avoid expensive
    # before/after/reply context hydration for up to 160 anchor-pool candidates.
    anchor_pool: set[str] = set()
    for anchor in case.anchors[:8]:
        for item in backend.search(SearchQuery(
            raw_query=anchor,
            evidence_limit=100,
            candidate_limit=160,
            include_context=False,
        )):
            anchor_pool.add(item.message.source_locator)
    selected_refs = {item.message.source_locator for item in top}
    recall_proxy = (len(selected_refs & anchor_pool) / len(anchor_pool)) if anchor_pool else None

    authors = {
        item.message.author_normalized or item.message.author
        for item in top
        if item.message.author_normalized or item.message.author
    }
    gate_passed, gate_reason = _gate(case, result_count=len(candidates), relevant_top_k=relevant)
    return SemanticCaseReport(
        name=case.name,
        question=case.question,
        expectation=case.expectation,
        latency_ms=round(latency_ms, 3),
        query_runs=retrieval.query_runs,
        duplicate_queries_skipped=retrieval.duplicate_queries_skipped,
        families_with_hits=retrieval.families_with_hits,
        results=len(candidates),
        top_k=len(top),
        proxy_relevant_top_k=relevant,
        top_k_relevance_proxy=round(relevance_proxy, 4) if relevance_proxy is not None else None,
        irrelevant_candidate_rate_proxy=round(irrelevant_proxy, 4) if irrelevant_proxy is not None else None,
        bounded_anchor_pool=len(anchor_pool),
        anchor_recall_proxy=round(recall_proxy, 4) if recall_proxy is not None else None,
        context_only_relevant_hits=context_only,
        independent_authors_top_k=len(authors),
        top_score=round(top[0].local_score, 6) if top else None,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
    )


def _gate(case: SemanticEvalCase, *, result_count: int, relevant_top_k: int) -> tuple[bool, str]:
    if case.expectation == "absent":
        return (result_count == 0, "expected no retrieval candidates")
    if case.expectation == "present":
        if result_count == 0:
            return False, "expected archive candidates but none were retrieved"
        if case.anchors and relevant_top_k == 0:
            return False, "retrieved candidates lacked topic anchors even after context expansion"
        return True, "expected archive evidence retrieved"
    return True, "observational metric only"


def _plan(
    intent: str,
    concepts: tuple[str, ...],
    families: tuple[SearchFamily, ...],
    *,
    entity_types: tuple[str, ...] = (),
    reply_context: bool = True,
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
    )


__all__ = [
    "SemanticBenchmarkReport",
    "SemanticCaseReport",
    "SemanticEvalCase",
    "default_semantic_cases",
    "semantic_benchmark_archive",
]
