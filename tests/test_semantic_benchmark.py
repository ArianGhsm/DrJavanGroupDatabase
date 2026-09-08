from pathlib import Path

from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.semantic_benchmark import SemanticEvalCase, semantic_benchmark_archive


def _plan(query: str) -> SearchPlan:
    return SearchPlan(
        searchable=True,
        intent="test_lookup",
        core_concepts=(query,),
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=(SearchFamily("topic", (query,)),),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
    )


def test_semantic_benchmark_reports_proxy_metrics_and_strict_gates(basic_archive: Path):
    cases = (
        SemanticEvalCase(
            name="rct_present",
            question="RCT؟",
            plan=_plan("RCT"),
            anchors=("rct",),
            expectation="present",
        ),
        SemanticEvalCase(
            name="sentinel_absent",
            question="never_present_9f7b",
            plan=_plan("never_present_9f7b"),
            anchors=("never_present_9f7b",),
            expectation="absent",
        ),
    )
    report = semantic_benchmark_archive(basic_archive, cases=cases, top_k=5)

    assert report.passed
    assert report.message_count == 8 and report.archive_files == 2
    assert report.median_retrieval_ms is not None
    assert report.p95_retrieval_ms is not None
    assert report.p95_retrieval_ms >= report.median_retrieval_ms

    present, absent = report.cases
    assert present.results > 0 and present.proxy_relevant_top_k > 0
    assert present.top_k_relevance_proxy is not None
    assert present.irrelevant_candidate_rate_proxy is not None
    assert present.first_relevant_discussion_rank == 1
    assert present.reciprocal_rank_proxy == 1.0
    assert present.bounded_anchor_pool > 0
    assert present.anchor_recall_proxy is not None
    assert present.bounded_anchor_discussion_pool > 0
    assert present.discussion_recall_proxy is not None
    assert present.independent_threads_top_k > 0
    assert present.discussion_count > 0
    assert present.retrieval_quality_state != "no_candidates"
    assert present.gate_passed

    assert absent.results == 0
    assert absent.first_relevant_discussion_rank is None
    assert absent.reciprocal_rank_proxy is None
    assert absent.gate_passed
