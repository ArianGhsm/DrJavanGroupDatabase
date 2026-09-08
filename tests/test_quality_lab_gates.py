from dataclasses import replace

from drjavanbot.ai.eval.gates import quality_gate_failures
from drjavanbot.ai.eval.schema import AggregateMetrics, GroundingMetrics, ScriptedMetrics


def _aggregate() -> AggregateMetrics:
    return AggregateMetrics(
        case_count=37,
        present_cases=4,
        absent_cases=4,
        observe_cases=29,
        strict_failures=0,
        discussion_recall_at_k_mean=1.0,
        mrr=0.7812,
        topic_relevance_at_k_mean=0.8958,
        facet_colocation_rate=1.0,
        irrelevant_candidate_rate_mean=0.1042,
        context_only_recovery_rate_mean=0.0089,
        false_insufficient_rate=0.0,
        false_supported_rate=0.0,
        median_retrieval_ms=2869.332,
        p95_retrieval_ms=3073.628,
        median_query_count=4.0,
        median_hydration_count=12.0,
    )


def _grounding() -> GroundingMetrics:
    return GroundingMetrics(
        total=12,
        passed=12,
        invalid_citation_rate=0.0,
        quote_mismatch_rate=0.0,
        unsupported_high_risk_claim_rate=0.0,
        verifier_accuracy=1.0,
    )


def _scripted() -> ScriptedMetrics:
    return ScriptedMetrics(
        total=5,
        passed=5,
        false_insufficient_rate=0.0,
        false_supported_rate=0.0,
        reason_code_accuracy=1.0,
        answerable_fragmented_recovery_rate=1.0,
        logical_calls_by_case={"hard": 4},
        token_budget_by_case={"hard": 520},
    )


def test_measured_baseline_has_headroom_and_passes_frozen_gates():
    assert quality_gate_failures(_aggregate(), _grounding(), _scripted(), index_seconds=277.181) == ()


def test_material_relevance_regression_fails_without_changing_thresholds():
    aggregate = replace(_aggregate(), mrr=0.69, topic_relevance_at_k_mean=0.84)
    failures = quality_gate_failures(aggregate, _grounding(), _scripted(), index_seconds=277.181)
    assert "quality:mrr" in failures
    assert "quality:topic_relevance" in failures


def test_false_supported_grounding_and_call_budget_regressions_are_zero_tolerance():
    aggregate = replace(_aggregate(), false_supported_rate=0.01)
    grounding = replace(_grounding(), verifier_accuracy=0.99, unsupported_high_risk_claim_rate=0.1)
    scripted = replace(_scripted(), logical_calls_by_case={"hard": 5})
    failures = quality_gate_failures(aggregate, grounding, scripted, index_seconds=277.181)
    assert "answerability:false_supported" in failures
    assert "grounding:verifier_accuracy" in failures
    assert "grounding:unsupported_high_risk_claim" in failures
    assert "efficiency:logical_ai_calls" in failures


def test_performance_thresholds_have_bounded_ci_headroom_not_unlimited_slack():
    aggregate = replace(_aggregate(), p95_retrieval_ms=4000.001)
    failures = quality_gate_failures(aggregate, _grounding(), _scripted(), index_seconds=360.001)
    assert "performance:p95_retrieval_ms" in failures
    assert "performance:index_seconds" in failures
