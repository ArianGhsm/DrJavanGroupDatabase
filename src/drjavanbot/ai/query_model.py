from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Iterable

from drjavanbot.normalization import normalize_text

QUERY_MODEL_VERSION = "query-understanding-v1"
MAX_INITIAL_FAMILIES = 8
MAX_FINAL_FAMILIES = 10
MAX_QUERIES_PER_FAMILY = 4
MAX_TOTAL_QUERIES = 20


class AnswerFacet(StrEnum):
    TOPIC = "topic"
    TIMING_AGE = "timing_age"
    TIMING = "timing"
    POPULATION = "population"
    PEDIATRIC_POPULATION = "pediatric_population"
    CONDITION = "condition"
    COMPARISON = "comparison"
    RECOMMENDATION = "recommendation"
    CAUSE_REASON = "cause_reason"
    METHOD_HOW = "method_how"
    QUANTITY = "quantity"
    DOSAGE = "dosage"
    INDICATION = "indication"
    COMPLICATION = "complication"
    PROGNOSIS = "prognosis"
    STAGE = "stage"


class EvidencePattern(StrEnum):
    SINGLE_MESSAGE = "single_message"
    FRAGMENTED_DISCUSSION = "fragmented_discussion"
    REPLY_CONTEXT = "reply_context"
    MULTI_SOURCE = "multi_source"


class RetrievalDepth(StrEnum):
    DIRECT = "direct"
    STANDARD = "standard"
    DEEP = "deep"


class FamilyPurpose(StrEnum):
    TOPIC = "topic"
    ENTITY = "entity"
    FACET = "facet"
    POPULATION = "population"
    INTERSECTION = "intersection"
    TERMINOLOGY = "terminology"
    STAGE = "stage"
    ALIAS = "alias"
    RESCUE = "rescue"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """Bounded retrieval policy. It describes search work, never a clinical answer."""

    depth: str = RetrievalDepth.STANDARD
    query_budget: int = 12
    family_budget: int = 6
    per_family_budget: int = 3
    rescue_allowed: bool = True
    max_rescue_families: int = 3
    expected_evidence_pattern: str = EvidencePattern.SINGLE_MESSAGE
    stop_when_required_facets_covered: bool = True
    minimum_family_coverage: int = 1

    def bounded(self) -> "RetrievalPolicy":
        return replace(
            self,
            depth=self.depth if self.depth in {x.value for x in RetrievalDepth} else RetrievalDepth.STANDARD,
            query_budget=max(1, min(int(self.query_budget), MAX_TOTAL_QUERIES)),
            family_budget=max(1, min(int(self.family_budget), MAX_FINAL_FAMILIES)),
            per_family_budget=max(1, min(int(self.per_family_budget), MAX_QUERIES_PER_FAMILY)),
            max_rescue_families=max(0, min(int(self.max_rescue_families), 4)),
            expected_evidence_pattern=(
                self.expected_evidence_pattern
                if self.expected_evidence_pattern in {x.value for x in EvidencePattern}
                else EvidencePattern.SINGLE_MESSAGE
            ),
            minimum_family_coverage=max(1, min(int(self.minimum_family_coverage), MAX_FINAL_FAMILIES)),
        )

    def to_dict(self) -> dict[str, Any]:
        value = self.bounded()
        return {
            "depth": str(value.depth),
            "query_budget": value.query_budget,
            "family_budget": value.family_budget,
            "per_family_budget": value.per_family_budget,
            "rescue_allowed": value.rescue_allowed,
            "max_rescue_families": value.max_rescue_families,
            "expected_evidence_pattern": str(value.expected_evidence_pattern),
            "stop_when_required_facets_covered": value.stop_when_required_facets_covered,
            "minimum_family_coverage": value.minimum_family_coverage,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RetrievalPolicy":
        if not isinstance(value, dict):
            return cls()
        return cls(
            depth=str(value.get("depth", RetrievalDepth.STANDARD)),
            query_budget=_safe_int(value.get("query_budget"), 12),
            family_budget=_safe_int(value.get("family_budget"), 6),
            per_family_budget=_safe_int(value.get("per_family_budget"), 3),
            rescue_allowed=_safe_bool(value.get("rescue_allowed"), True),
            max_rescue_families=_safe_int(value.get("max_rescue_families"), 3),
            expected_evidence_pattern=str(value.get("expected_evidence_pattern", EvidencePattern.SINGLE_MESSAGE)),
            stop_when_required_facets_covered=_safe_bool(value.get("stop_when_required_facets_covered"), True),
            minimum_family_coverage=_safe_int(value.get("minimum_family_coverage"), 1),
        ).bounded()


@dataclass(frozen=True, slots=True)
class SearchFamily:
    name: str
    queries: tuple[str, ...]
    purpose: str = FamilyPurpose.OTHER
    priority: int = 50
    anchor: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "queries": list(self.queries),
            "purpose": str(self.purpose),
            "priority": max(0, min(int(self.priority), 100)),
            "anchor": bool(self.anchor),
        }


@dataclass(frozen=True, slots=True)
class SearchPlan:
    """Versioned query-understanding contract consumed by local retrieval.

    Legacy fields are retained so retrieval can consume this object without
    knowing anything about the planner prompt or the typed v1 additions.
    """

    searchable: bool
    intent: str
    core_concepts: tuple[str, ...]
    aliases: tuple[str, ...]
    optional_concepts: tuple[str, ...]
    entity_types: tuple[str, ...]
    query_families: tuple[SearchFamily, ...]
    phrases: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    low_information_terms: tuple[str, ...]
    reply_context: bool = True
    required_aspects: tuple[str, ...] = ()
    schema_version: str = QUERY_MODEL_VERSION
    normalized_intent: str = ""
    topic_anchors: tuple[str, ...] = ()
    answer_facets: tuple[str, ...] = ()
    population_constraints: tuple[str, ...] = ()
    condition_constraints: tuple[str, ...] = ()
    temporal_constraints: tuple[str, ...] = ()
    comparison_targets: tuple[str, ...] = ()
    terminology_hints: tuple[str, ...] = ()
    colloquial_hints: tuple[str, ...] = ()
    typo_hints: tuple[str, ...] = ()
    intersection_queries: tuple[str, ...] = ()
    negative_hints: tuple[str, ...] = ()
    expected_evidence_pattern: str = EvidencePattern.SINGLE_MESSAGE
    retrieval_policy: RetrievalPolicy = RetrievalPolicy()

    @property
    def queries(self) -> tuple[tuple[str, str], ...]:
        """Family-balanced schedule: every family gets a chance before variants."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        policy = self.retrieval_policy.bounded()
        families = tuple(
            sorted(
                self.query_families[: min(policy.family_budget, MAX_FINAL_FAMILIES)],
                key=lambda family: (-int(family.priority), self.query_families.index(family)),
            )
        )
        query_budget = min(policy.query_budget, MAX_TOTAL_QUERIES)
        per_family = min(policy.per_family_budget, MAX_QUERIES_PER_FAMILY)
        for query_index in range(per_family):
            for family in families:
                if query_index >= len(family.queries):
                    continue
                value = family.queries[query_index]
                key = _near_duplicate_key(value)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append((family.name, value))
                if len(out) >= query_budget:
                    return tuple(out)
        return tuple(out)

    @property
    def anchor_families(self) -> tuple[SearchFamily, ...]:
        explicit = tuple(family for family in self.query_families if family.anchor)
        if explicit:
            return explicit
        return tuple(
            family for family in self.query_families
            if str(family.purpose) in {FamilyPurpose.TOPIC, FamilyPurpose.ENTITY, FamilyPurpose.INTERSECTION}
        )

    def with_added_families(self, families: Iterable[SearchFamily]) -> "SearchPlan":
        merged: list[SearchFamily] = list(self.query_families[:MAX_INITIAL_FAMILIES])
        names = {family.name.casefold() for family in merged}
        existing = {_near_duplicate_key(q) for family in merged for q in family.queries if _near_duplicate_key(q)}
        for family in families:
            if len(merged) >= MAX_FINAL_FAMILIES:
                break
            fresh: list[str] = []
            for query in family.queries:
                key = _near_duplicate_key(query)
                if not key or key in existing:
                    continue
                existing.add(key)
                fresh.append(query)
                if len(fresh) >= MAX_QUERIES_PER_FAMILY:
                    break
            if not fresh:
                continue
            name = family.name
            if name.casefold() in names:
                name = f"{name}-refined"
            merged.append(replace(family, name=name, queries=tuple(fresh)))
            names.add(name.casefold())
        return replace(self, query_families=tuple(merged))

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "intent": self.intent,
            "normalized_intent": self.normalized_intent or self.intent,
            "topic_anchors": list((self.topic_anchors or self.core_concepts)[:8]),
            "required_aspects": list(self.required_aspects[:10]),
            "answer_facets": list(self.answer_facets[:10]),
            "query_families": [family.to_dict() for family in self.query_families[:MAX_FINAL_FAMILIES]],
            "anchor_families": [family.name for family in self.anchor_families[:6]],
            "expected_evidence_pattern": self.expected_evidence_pattern,
            "retrieval_policy": self.retrieval_policy.to_dict(),
            "reply_context": self.reply_context,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "searchable": self.searchable,
            "intent": self.intent,
            "core_concepts": list(self.core_concepts),
            "aliases": list(self.aliases),
            "optional_concepts": list(self.optional_concepts),
            "entity_types": list(self.entity_types),
            "query_families": [family.to_dict() for family in self.query_families],
            "phrases": list(self.phrases),
            "exclude_terms": list(self.exclude_terms),
            "low_information_terms": list(self.low_information_terms),
            "reply_context": self.reply_context,
            "required_aspects": list(self.required_aspects),
            "schema_version": self.schema_version,
            "normalized_intent": self.normalized_intent or self.intent,
            "topic_anchors": list(self.topic_anchors),
            "answer_facets": list(self.answer_facets),
            "population_constraints": list(self.population_constraints),
            "condition_constraints": list(self.condition_constraints),
            "temporal_constraints": list(self.temporal_constraints),
            "comparison_targets": list(self.comparison_targets),
            "terminology_hints": list(self.terminology_hints),
            "colloquial_hints": list(self.colloquial_hints),
            "typo_hints": list(self.typo_hints),
            "intersection_queries": list(self.intersection_queries),
            "negative_hints": list(self.negative_hints),
            "expected_evidence_pattern": self.expected_evidence_pattern,
            "retrieval_policy": self.retrieval_policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, question: str = "") -> "SearchPlan":
        # Late import avoids a query_model <-> planner import cycle.
        from .planner import plan_from_payload
        return plan_from_payload(value, question=question)


def _near_duplicate_key(value: str) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    tokens = normalized.split()
    if len(tokens) > 1:
        return " ".join(sorted(dict.fromkeys(tokens)))
    return normalized


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


__all__ = [
    "QUERY_MODEL_VERSION", "MAX_INITIAL_FAMILIES", "MAX_FINAL_FAMILIES",
    "MAX_QUERIES_PER_FAMILY", "MAX_TOTAL_QUERIES", "AnswerFacet",
    "EvidencePattern", "RetrievalDepth", "FamilyPurpose", "RetrievalPolicy",
    "SearchFamily", "SearchPlan",
]
