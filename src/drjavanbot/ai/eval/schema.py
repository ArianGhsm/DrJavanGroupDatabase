from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

REPORT_SCHEMA_VERSION = "quality-lab-v2.0"
Expectation = Literal["present", "absent", "observe"]
FailureOwner = Literal["planner", "retrieval", "answerability_synthesis", "validation_provider", "none"]


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    category: str
    question: str
    expectation: Expectation
    query_families: tuple[tuple[str, tuple[str, ...]], ...]
    topic_anchors: tuple[str, ...] = ()
    required_facets: tuple[tuple[str, ...], ...] = ()
    known_answerable: bool = False
    privacy_sensitive: bool = False
    reply_context: bool = True
    min_discussion_recall: float = 1.0
    gold_discussion_hashes: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CaseMetrics:
    case_id: str
    category: str
    expectation: Expectation
    known_answerable: bool
    latency_ms: float
    query_count: int
    duplicate_queries_skipped: int
    duplicate_family_queries: int
    hydration_count: int
    candidate_count: int
    discussion_count: int
    first_relevant_discussion_rank: int | None
    reciprocal_rank: float
    discussion_recall_at_k: float | None
    topic_relevance_at_k: float | None
    required_facets_total: int
    required_facets_colocated: int
    facet_colocation_complete: bool | None
    irrelevant_candidate_rate: float | None
    duplicate_thread_concentration: float
    author_diversity: int
    context_only_recovery_rate: float | None
    generic_noise_rate: float
    supported_answer_observed: bool
    insufficient_observed: bool
    gate_passed: bool
    reason_code: str
    failure_owner: FailureOwner
    top_discussion_hashes: tuple[str, ...] = ()
    relevant_discussion_hashes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GroundingMetrics:
    total: int
    passed: int
    invalid_citation_rate: float
    quote_mismatch_rate: float
    unsupported_high_risk_claim_rate: float
    verifier_accuracy: float
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScriptedMetrics:
    total: int
    passed: int
    false_insufficient_rate: float
    false_supported_rate: float
    reason_code_accuracy: float
    answerable_fragmented_recovery_rate: float
    logical_calls_by_case: dict[str, int] = field(default_factory=dict)
    token_budget_by_case: dict[str, int] = field(default_factory=dict)
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    case_count: int
    present_cases: int
    absent_cases: int
    observe_cases: int
    strict_failures: int
    discussion_recall_at_k_mean: float | None
    mrr: float
    topic_relevance_at_k_mean: float | None
    facet_colocation_rate: float | None
    irrelevant_candidate_rate_mean: float | None
    context_only_recovery_rate_mean: float | None
    false_insufficient_rate: float | None
    false_supported_rate: float | None
    median_retrieval_ms: float | None
    p95_retrieval_ms: float | None
    median_query_count: float | None
    median_hydration_count: float | None


@dataclass(frozen=True, slots=True)
class QualityReport:
    base_sha: str
    schema_version: str
    top_k: int
    index_seconds: float
    db_size_bytes: int
    archive_files: int
    message_count: int
    cases: tuple[CaseMetrics, ...]
    aggregate: AggregateMetrics
    grounding: GroundingMetrics
    scripted: ScriptedMetrics
    strict_failures: tuple[str, ...]
    pii_safe: bool = True

    @property
    def passed(self) -> bool:
        return not self.strict_failures and not self.grounding.failures and not self.scripted.failures

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload
