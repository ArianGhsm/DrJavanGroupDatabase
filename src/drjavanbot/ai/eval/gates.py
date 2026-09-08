from __future__ import annotations

from .schema import AggregateMetrics, GroundingMetrics, ScriptedMetrics

# Frozen only after measuring BASE_SHA 4226f2c30a04d89a28afe8284be5c3e5f657a2a6.
# Deterministic correctness gates keep the baseline value where appropriate;
# runtime/ranking gates include bounded CI variance headroom without masking a
# material regression.
MIN_DISCUSSION_RECALL_MEAN = 1.0
MIN_MRR = 0.70
MIN_TOPIC_RELEVANCE_MEAN = 0.85
MIN_FACET_COLOCATION_RATE = 0.95
MAX_IRRELEVANT_CANDIDATE_RATE_MEAN = 0.15
MAX_P95_RETRIEVAL_MS = 4_000.0
MAX_INDEX_SECONDS = 360.0
MAX_FALSE_INSUFFICIENT_RATE = 0.0
MAX_FALSE_SUPPORTED_RATE = 0.0
MIN_GROUNDING_VERIFIER_ACCURACY = 1.0
MAX_LOGICAL_AI_CALLS = 4
MAX_MEDIAN_QUERY_COUNT = 6.0
MAX_MEDIAN_HYDRATION_COUNT = 16.0


def quality_gate_failures(
    aggregate: AggregateMetrics,
    grounding: GroundingMetrics,
    scripted: ScriptedMetrics,
    *,
    index_seconds: float,
) -> tuple[str, ...]:
    """Return stable, PII-free gate reason codes for a completed report."""
    failures: list[str] = []

    def require_min(value: float | None, minimum: float, code: str) -> None:
        if value is None or value < minimum:
            failures.append(code)

    def require_max(value: float | None, maximum: float, code: str) -> None:
        if value is None or value > maximum:
            failures.append(code)

    require_min(aggregate.discussion_recall_at_k_mean, MIN_DISCUSSION_RECALL_MEAN, "quality:discussion_recall")
    require_min(aggregate.mrr, MIN_MRR, "quality:mrr")
    require_min(aggregate.topic_relevance_at_k_mean, MIN_TOPIC_RELEVANCE_MEAN, "quality:topic_relevance")
    require_min(aggregate.facet_colocation_rate, MIN_FACET_COLOCATION_RATE, "quality:facet_colocation")
    require_max(aggregate.irrelevant_candidate_rate_mean, MAX_IRRELEVANT_CANDIDATE_RATE_MEAN, "quality:irrelevant_candidates")
    require_max(aggregate.p95_retrieval_ms, MAX_P95_RETRIEVAL_MS, "performance:p95_retrieval_ms")
    require_max(float(index_seconds), MAX_INDEX_SECONDS, "performance:index_seconds")
    require_max(aggregate.false_insufficient_rate, MAX_FALSE_INSUFFICIENT_RATE, "answerability:false_insufficient")
    require_max(aggregate.false_supported_rate, MAX_FALSE_SUPPORTED_RATE, "answerability:false_supported")
    require_min(grounding.verifier_accuracy, MIN_GROUNDING_VERIFIER_ACCURACY, "grounding:verifier_accuracy")
    require_max(aggregate.median_query_count, MAX_MEDIAN_QUERY_COUNT, "efficiency:query_count")
    require_max(aggregate.median_hydration_count, MAX_MEDIAN_HYDRATION_COUNT, "efficiency:hydration_count")

    if max(scripted.logical_calls_by_case.values(), default=0) > MAX_LOGICAL_AI_CALLS:
        failures.append("efficiency:logical_ai_calls")
    if grounding.invalid_citation_rate > 0:
        failures.append("grounding:invalid_citation")
    if grounding.quote_mismatch_rate > 0:
        failures.append("grounding:quote_mismatch")
    if grounding.unsupported_high_risk_claim_rate > 0:
        failures.append("grounding:unsupported_high_risk_claim")
    if scripted.reason_code_accuracy < 1.0:
        failures.append("answerability:reason_code_accuracy")
    if scripted.answerable_fragmented_recovery_rate < 1.0:
        failures.append("answerability:fragmented_recovery")

    return tuple(failures)
