from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

QUESTION_UNDERSTANDING_SCHEMA_VERSION = "question-intelligence-v2.0"
RETRIEVAL_CONTRACT_VERSION = "retrieval-provider-v2.0"
EVIDENCE_CONTRACT_VERSION = "evidence-v2.0"
ANSWERABILITY_CONTRACT_VERSION = "requested-fact-v2.0"


class LanguageProfile(StrEnum):
    PERSIAN = "persian"
    ENGLISH = "english"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class QuestionDomain(StrEnum):
    DENTISTRY = "dentistry"
    CAREER_ECONOMICS = "career_economics"
    REGULATION = "regulation"
    GENERAL = "general"
    UNKNOWN = "unknown"


class QuestionIntent(StrEnum):
    FACTUAL = "factual"
    DEFINITION = "definition"
    CLASSIFICATION = "classification"
    COMPARISON = "comparison"
    RECOMMENDATION = "recommendation"
    DIAGNOSIS = "diagnosis"
    DIFFERENTIAL_DIAGNOSIS = "differential_diagnosis"
    TREATMENT = "treatment"
    TECHNIQUE = "technique"
    CAREER = "career"
    REGULATORY = "regulatory"
    ARCHIVE_OPINION = "archive_opinion"
    CURRENT_INFORMATION = "current_information"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class FreshnessClass(StrEnum):
    EVERGREEN = "evergreen"
    RECENT = "recent"
    CURRENT = "current"
    REALTIME = "realtime"
    UNSPECIFIED = "unspecified"


class ConfidenceClass(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SafetyClass(StrEnum):
    GENERAL = "general"
    CLINICAL = "clinical"
    MEDICATION = "medication"
    HIGH_STAKES = "high_stakes"


class SourceType(StrEnum):
    ARCHIVE = "archive"
    DENTAL_KNOWLEDGE = "dental_knowledge"
    SCIENTIFIC = "scientific"
    CURRENT_WEB = "current_web"
    OFFICIAL = "official"
    NONE = "none"


class SourceRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class ConceptResolution:
    concept_id: str
    canonical: str
    matched_text: str
    variants: tuple[str, ...]
    entity_type: str = "dental_term"
    subdomain: str | None = None
    confidence: float = 1.0
    ambiguity: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityMention:
    text: str
    canonical_id: str
    canonical_label: str
    entity_type: str
    variants: tuple[str, ...] = ()
    inferred: bool = False
    confidence: float = 1.0
    subdomain: str | None = None


@dataclass(frozen=True, slots=True)
class QuestionConstraints:
    population: tuple[str, ...] = ()
    temporal: tuple[str, ...] = ()
    comparison_targets: tuple[str, ...] = ()
    career_stage: tuple[str, ...] = ()
    profession: tuple[str, ...] = ()
    other: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Geography:
    country_code: str | None = None
    label: str | None = None
    explicit: bool = False
    source: str = "none"


@dataclass(frozen=True, slots=True)
class Ambiguity:
    kind: str
    span: str
    candidates: tuple[str, ...]
    resolved_to: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class QuestionUnderstanding:
    normalized_question: str
    language_profile: str
    domain: str
    subdomain: str | None
    intent: str
    entities: tuple[EntityMention, ...]
    facets: tuple[str, ...]
    constraints: QuestionConstraints = QuestionConstraints()
    geography: Geography = Geography()
    freshness: str = FreshnessClass.UNSPECIFIED
    source_preferences: tuple[str, ...] = ()
    archive_specific: bool = False
    scientific_evidence_needed: bool = False
    current_information_needed: bool = False
    ambiguity: tuple[Ambiguity, ...] = ()
    confidence_class: str = ConfidenceClass.MEDIUM
    safety_class: str = SafetyClass.GENERAL
    schema_version: str = QUESTION_UNDERSTANDING_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "normalized_question": self.normalized_question,
            "language_profile": str(self.language_profile),
            "domain": str(self.domain),
            "subdomain": self.subdomain,
            "intent": str(self.intent),
            "entities": [
                {
                    "text": item.text,
                    "canonical_id": item.canonical_id,
                    "canonical_label": item.canonical_label,
                    "entity_type": item.entity_type,
                    "variants": list(item.variants),
                    "inferred": item.inferred,
                    "confidence": item.confidence,
                    "subdomain": item.subdomain,
                }
                for item in self.entities
            ],
            "facets": list(self.facets),
            "constraints": {
                "population": list(self.constraints.population),
                "temporal": list(self.constraints.temporal),
                "comparison_targets": list(self.constraints.comparison_targets),
                "career_stage": list(self.constraints.career_stage),
                "profession": list(self.constraints.profession),
                "other": list(self.constraints.other),
            },
            "geography": {
                "country_code": self.geography.country_code,
                "label": self.geography.label,
                "explicit": self.geography.explicit,
                "source": self.geography.source,
            },
            "freshness": str(self.freshness),
            "source_preferences": list(self.source_preferences),
            "archive_specific": self.archive_specific,
            "scientific_evidence_needed": self.scientific_evidence_needed,
            "current_information_needed": self.current_information_needed,
            "ambiguity": [
                {
                    "kind": item.kind,
                    "span": item.span,
                    "candidates": list(item.candidates),
                    "resolved_to": item.resolved_to,
                    "confidence": item.confidence,
                }
                for item in self.ambiguity
            ],
            "confidence_class": str(self.confidence_class),
            "safety_class": str(self.safety_class),
        }


@dataclass(frozen=True, slots=True)
class SourceSelection:
    source_type: str
    priority: int
    requirement: str
    freshness_requirement: str
    rationale_code: str
    query_strategy: str


@dataclass(frozen=True, slots=True)
class SourceRoute:
    selected_sources: tuple[SourceSelection, ...]
    fallback_order: tuple[str, ...]
    rationale_codes: tuple[str, ...]

    @property
    def required_sources(self) -> tuple[str, ...]:
        return tuple(
            item.source_type
            for item in self.selected_sources
            if item.requirement == SourceRequirement.REQUIRED
        )


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str
    family: str
    purpose: str = "topic"
    priority: int = 50
    anchor: bool = False
    mandatory: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    source_type: str
    normalized_question: str
    queries: tuple[RetrievalQuery, ...]
    entity_ids: tuple[str, ...]
    facets: tuple[str, ...]
    freshness: str
    geography: Geography
    top_k: int = 20
    contract_version: str = RETRIEVAL_CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    source_type: str
    source_name: str
    source_ref: str
    text: str
    title: str | None = None
    context: str | None = None
    timestamp: str | None = None
    author_or_org: str | None = None
    retrieval_score: float | None = None
    semantic_score: float | None = None
    freshness: str = FreshnessClass.UNSPECIFIED
    metadata: dict[str, Any] = field(default_factory=dict)
    citation_capability: str = "direct"
    trust_tier: str = "unknown"
    url: str | None = None
    publication_year: int | None = None
    publication_type: str | None = None
    retrieved_at: str | None = None
    trust_score: float | None = None
    methodological_strength: float | None = None
    independence_key: str | None = None
    contract_version: str = EVIDENCE_CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    source_type: str
    items: tuple[EvidenceItem, ...]
    query_count: int
    latency_ms: float
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FacetCoverage:
    facet: str
    supported: bool
    score: float
    signal_code: str


@dataclass(frozen=True, slots=True)
class RequestedFactCoverage:
    topic_present: bool
    requested_facets: tuple[FacetCoverage, ...]
    requested_fact_supported: bool
    evidence_directness: str
    evidence_count: int
    independent_sources: int
    conflicts: bool
    freshness_satisfied: bool
    source_requirement_satisfied: bool
    answerable: bool
    reason_code: str
    contract_version: str = ANSWERABILITY_CONTRACT_VERSION
