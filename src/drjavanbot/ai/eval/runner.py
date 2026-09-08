from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import statistics
import tempfile
import time

from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.normalization import normalize_text, tokenize
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.storage import full_reindex
from .gates import quality_gate_failures
from .golden import golden_cases
from .grounding import run_grounding_red_team
from .schema import AggregateMetrics, CaseMetrics, GoldenCase, QualityReport, REPORT_SCHEMA_VERSION
from .scripted import run_scripted_e2e

_GENERIC = {"خوب", "بد", "چی", "چیه", "چطور", "چرا", "سلام", "ممنون", "لطفا", "please", "good", "bad"}


@dataclass(frozen=True, slots=True)
class _Bundle:
    discussion_hash: str
    text: str
    direct_text: str
    author: str
    topic_hit: bool
    direct_topic_hit: bool
    facet_hits: int
    low_information: bool


def _plan(case: GoldenCase) -> SearchPlan:
    if not case.query_families:
        return SearchPlan(
            searchable=False,
            intent=case.category,
            core_concepts=(), aliases=(), optional_concepts=(), entity_types=(),
            query_families=(), phrases=(), exclude_terms=(), low_information_terms=(),
            reply_context=False, required_aspects=(),
        )
    return SearchPlan(
        searchable=True,
        intent=case.category,
        core_concepts=case.topic_anchors[:4] or (case.question,),
        aliases=case.topic_anchors[4:8],
        optional_concepts=tuple(group[0] for group in case.required_facets if group),
        entity_types=(),
        query_families=tuple(SearchFamily(name, queries) for name, queries in case.query_families),
        phrases=(), exclude_terms=(), low_information_terms=(),
        reply_context=case.reply_context,
        required_aspects=tuple(f"facet_{i+1}" for i in range(len(case.required_facets))),
    )


def _normalized_any(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(v for raw in values if (v := normalize_text(raw)))


def _bundle(candidate, case: GoldenCase) -> _Bundle:
    messages = (candidate.message, *candidate.context)
    text_parts = [normalize_text(m.text_normalized or m.text_raw) for m in messages]
    direct = text_parts[0] if text_parts else ""
    combined = " ".join(part for part in text_parts if part)
    topic_anchors = _normalized_any(case.topic_anchors)
    topic_hit = any(anchor in combined for anchor in topic_anchors) if topic_anchors else bool(combined)
    direct_topic_hit = any(anchor in direct for anchor in topic_anchors) if topic_anchors else bool(direct)
    facet_hits = 0
    for group in case.required_facets:
        normalized_group = _normalized_any(group)
        if normalized_group and any(anchor in combined for anchor in normalized_group):
            facet_hits += 1
    # Stable PII-free identity derived only from canonical archive position.
    raw_key = f"p{candidate.message.source_page}:b{candidate.message.source_order // 12}"
    discussion_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    tokens = tuple(tokenize(combined))
    low_information = bool(tokens) and len(tokens) <= 3 and all(token in _GENERIC for token in tokens)
    author = candidate.message.author_normalized or candidate.message.author or ""
    return _Bundle(discussion_hash, combined, direct, author, topic_hit, direct_topic_hit, facet_hits, low_information)


def evaluate_case(backend: SQLiteSearchBackend, case: GoldenCase, *, top_k: int) -> CaseMetrics:
    plan = _plan(case)
    raw_family_queries = [normalize_text(q) for _, queries in case.query_families for q in queries if normalize_text(q)]
    duplicate_family_queries = max(0, len(raw_family_queries) - len(set(raw_family_queries)))
    started = time.perf_counter()
    report = retrieve_with_plan(backend, plan, evidence_limit=max(24, top_k))
    latency_ms = (time.perf_counter() - started) * 1000.0
    top = tuple(report.candidates[:top_k])
    bundles = tuple(_bundle(candidate, case) for candidate in top)
    required_total = len(case.required_facets)

    relevant_indices: list[int] = []
    colocated_indices: list[int] = []
    context_only = 0
    for idx, bundle in enumerate(bundles, start=1):
        if bundle.topic_hit:
            relevant_indices.append(idx)
            if not bundle.direct_topic_hit:
                context_only += 1
            if bundle.facet_hits == required_total:
                colocated_indices.append(idx)

    first_relevant = (colocated_indices or relevant_indices or [None])[0]
    rr = round(1.0 / first_relevant, 4) if isinstance(first_relevant, int) and first_relevant > 0 else 0.0
    relevant_hashes = tuple(dict.fromkeys(
        bundles[i - 1].discussion_hash
        for i in (colocated_indices if required_total else relevant_indices)
    ))
    discussion_recall = None
    if case.expectation == "present":
        if case.gold_discussion_hashes:
            retrieved = set(relevant_hashes)
            gold = set(case.gold_discussion_hashes)
            discussion_recall = round(len(retrieved & gold) / len(gold), 4) if gold else 0.0
        else:
            # Baseline-discovery mode only. Final strict cases freeze BASE_SHA hashes.
            discussion_recall = 1.0 if relevant_hashes else 0.0

    topic_relevance = round(len(relevant_indices) / len(bundles), 4) if bundles and case.topic_anchors else None
    irrelevant_rate = round(1.0 - topic_relevance, 4) if topic_relevance is not None else None
    unique_discussions = {b.discussion_hash for b in bundles}
    if bundles:
        counts = {key: sum(1 for b in bundles if b.discussion_hash == key) for key in unique_discussions}
        concentration = round(max(counts.values(), default=0) / len(bundles), 4)
    else:
        concentration = 0.0
    authors = {b.author for b in bundles if b.author}
    context_recovery = round(context_only / len(relevant_indices), 4) if relevant_indices else None
    noise_rate = round(sum(1 for b in bundles if b.low_information) / len(bundles), 4) if bundles else 0.0
    facet_complete = None if not required_total else bool(colocated_indices)
    supported = bool(colocated_indices or (not required_total and relevant_indices))

    gate_passed = True
    reason = "observe_only"
    owner = "none"
    if case.expectation == "absent":
        # Retrieval may surface lexical/fallback candidates for an unseen query.
        # The safety contract is that none of them form relevant supported evidence.
        gate_passed = not supported
        if gate_passed:
            reason = "absent_clean" if not report.candidates else "absent_irrelevant_only"
            owner = "none"
        else:
            reason = "unexpected_relevant_evidence"
            owner = "retrieval"
    elif case.expectation == "present":
        if report.query_runs == 0:
            gate_passed, reason, owner = False, "planner_no_queries", "planner"
        elif not report.candidates:
            gate_passed, reason, owner = False, "retrieval_no_candidates", "retrieval"
        elif not relevant_indices:
            gate_passed, reason, owner = False, "no_relevant_discussion", "retrieval"
        elif required_total and not colocated_indices:
            gate_passed, reason, owner = False, "facet_not_colocated", "retrieval"
        elif case.gold_discussion_hashes and (discussion_recall or 0.0) < case.min_discussion_recall:
            gate_passed, reason, owner = False, "gold_discussion_not_recovered", "retrieval"
        elif noise_rate > 0.50:
            gate_passed, reason, owner = False, "generic_noise_dominates", "retrieval"
        else:
            gate_passed, reason, owner = True, "discussion_and_facets_recovered", "none"
    else:
        if report.query_runs == 0:
            reason, owner = "observe_planner_no_queries", "planner"
        elif not report.candidates:
            reason, owner = "observe_no_candidates", "retrieval"
        elif case.topic_anchors and not relevant_indices:
            reason, owner = "observe_no_relevant_discussion", "retrieval"
        elif required_total and not colocated_indices:
            reason, owner = "observe_facet_not_colocated", "retrieval"

    return CaseMetrics(
        case_id=case.case_id,
        category=case.category,
        expectation=case.expectation,
        known_answerable=case.known_answerable,
        latency_ms=round(latency_ms, 3),
        query_count=report.query_runs,
        duplicate_queries_skipped=report.duplicate_queries_skipped,
        duplicate_family_queries=duplicate_family_queries,
        hydration_count=report.context_hydrated,
        candidate_count=len(report.candidates),
        discussion_count=len(unique_discussions),
        first_relevant_discussion_rank=first_relevant if isinstance(first_relevant, int) else None,
        reciprocal_rank=rr,
        discussion_recall_at_k=discussion_recall,
        topic_relevance_at_k=topic_relevance,
        required_facets_total=required_total,
        required_facets_colocated=(required_total if colocated_indices else max((b.facet_hits for b in bundles if b.topic_hit), default=0)),
        facet_colocation_complete=facet_complete,
        irrelevant_candidate_rate=irrelevant_rate,
        duplicate_thread_concentration=concentration,
        author_diversity=len(authors),
        context_only_recovery_rate=context_recovery,
        generic_noise_rate=noise_rate,
        supported_answer_observed=supported,
        insufficient_observed=not supported,
        gate_passed=gate_passed,
        reason_code=reason,
        failure_owner=owner,  # type: ignore[arg-type]
        top_discussion_hashes=tuple(dict.fromkeys(b.discussion_hash for b in bundles[:5])),
        relevant_discussion_hashes=relevant_hashes[:5],
    )


def _mean(values):
    vals = [float(v) for v in values if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def _aggregate(cases: tuple[CaseMetrics, ...], scripted) -> AggregateMetrics:
    latencies = [c.latency_ms for c in cases]
    return AggregateMetrics(
        case_count=len(cases),
        present_cases=sum(c.expectation == "present" for c in cases),
        absent_cases=sum(c.expectation == "absent" for c in cases),
        observe_cases=sum(c.expectation == "observe" for c in cases),
        strict_failures=sum(not c.gate_passed for c in cases),
        discussion_recall_at_k_mean=_mean(c.discussion_recall_at_k for c in cases),
        mrr=round(statistics.fmean(c.reciprocal_rank for c in cases if c.expectation == "present"), 4) if any(c.expectation == "present" for c in cases) else 0.0,
        topic_relevance_at_k_mean=_mean(c.topic_relevance_at_k for c in cases if c.expectation == "present"),
        facet_colocation_rate=_mean(1.0 if c.facet_colocation_complete else 0.0 for c in cases if c.facet_colocation_complete is not None),
        irrelevant_candidate_rate_mean=_mean(c.irrelevant_candidate_rate for c in cases if c.expectation == "present"),
        context_only_recovery_rate_mean=_mean(c.context_only_recovery_rate for c in cases),
        false_insufficient_rate=scripted.false_insufficient_rate,
        false_supported_rate=scripted.false_supported_rate,
        median_retrieval_ms=round(statistics.median(latencies), 3) if latencies else None,
        p95_retrieval_ms=_p95(latencies),
        median_query_count=round(statistics.median(c.query_count for c in cases), 2) if cases else None,
        median_hydration_count=round(statistics.median(c.hydration_count for c in cases), 2) if cases else None,
    )


def run_quality_eval(archive_dir: Path, *, top_k: int = 12, base_sha: str = "unknown") -> QualityReport:
    bounded_top_k = max(1, min(int(top_k), 40))
    with tempfile.TemporaryDirectory(prefix="drjavan-quality-lab-") as temp:
        db_path = Path(temp) / "archive.sqlite3"
        index_report = full_reindex(archive_dir, db_path)
        backend = SQLiteSearchBackend(db_path)
        cases = tuple(evaluate_case(backend, case, top_k=bounded_top_k) for case in golden_cases())
        grounding = run_grounding_red_team()
        scripted = run_scripted_e2e()
        aggregate = _aggregate(cases, scripted)
        strict_failures = [c.case_id for c in cases if not c.gate_passed]
        strict_failures.extend(
            quality_gate_failures(
                aggregate,
                grounding,
                scripted,
                index_seconds=index_report.elapsed_seconds,
            )
        )
        return QualityReport(
            base_sha=base_sha,
            schema_version=REPORT_SCHEMA_VERSION,
            top_k=bounded_top_k,
            index_seconds=round(index_report.elapsed_seconds, 3),
            db_size_bytes=index_report.db_size_bytes,
            archive_files=index_report.archive_files,
            message_count=index_report.messages,
            cases=cases,
            aggregate=aggregate,
            grounding=grounding,
            scripted=scripted,
            strict_failures=tuple(strict_failures),
            pii_safe=True,
        )
