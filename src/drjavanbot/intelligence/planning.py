from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Protocol

from drjavanbot.normalization import normalize_text
from .models import (
    ConfidenceClass,
    EntityMention,
    FreshnessClass,
    Geography,
    QuestionDomain,
    QuestionIntent,
    QuestionUnderstanding,
    SafetyClass,
    QUESTION_UNDERSTANDING_SCHEMA_VERSION,
)
from .model_policy import ModelDecision, ModelPolicy, ModelStage
from .facets import FACET_BY_NAME
from .understanding import QuestionContext, understand_question


QUESTION_INTELLIGENCE_SYSTEM_PROMPT = f"""You are the question-intelligence stage for a dental assistant. Return compact JSON only.
Do NOT answer the user's question and do NOT invent clinical facts, prevalence values, doses, salaries, product recommendations, or guidelines.
Your job is semantic classification before retrieval: domain, subdomain, intent, entities/terminology, requested facets, constraints, geography/freshness, source need, and real ambiguity.
Search/source hints are not evidence.

Schema version: {QUESTION_UNDERSTANDING_SCHEMA_VERSION}
Compact shape:
{{"schema_version":"{QUESTION_UNDERSTANDING_SCHEMA_VERSION}","domain":"dentistry|career_economics|regulation|general|unknown","subdomain":null,"intent":"factual|definition|classification|comparison|recommendation|diagnosis|differential_diagnosis|treatment|technique|career|regulatory|archive_opinion|current_information|hybrid|unknown","entities":[{{"text":"...","canonical":"...","type":"..."}}],"facets":["..."],"constraints":{{"population":[],"temporal":[],"comparison_targets":[],"career_stage":[],"profession":[]}},"geography":{{"country_code":null,"label":null,"explicit":false}},"freshness":"evergreen|recent|current|realtime|unspecified","archive_specific":false,"scientific_evidence_needed":false,"current_information_needed":false,"ambiguity":[{{"kind":"...","span":"...","candidates":["..."],"resolved_to":null}}],"confidence_class":"high|medium|low","safety_class":"general|clinical|medication|high_stakes"}}
Use requested-fact facets, not generic question words. For example, commonness/prevalence wording is a prevalence/frequency facet; "which" alone is not a recommendation.
For current salary/cost/market/regulatory questions set current_information_needed=true. For explicit group/archive-opinion questions set archive_specific=true. For standard evergreen dentistry facts prefer scientific_evidence_needed=true unless the request is explicitly archive-only."""


class IntelligenceModelProvider(Protocol):
    def generate_json(self, *, system_prompt: str, user_prompt: str, decision: ModelDecision) -> str:
        ...


class QuestionIntelligenceEngine:
    """LLM-first semantic understanding with deterministic fail-soft augmentation."""

    def __init__(
        self,
        *,
        provider: IntelligenceModelProvider | None = None,
        model_policy: ModelPolicy | None = None,
        context: QuestionContext | None = None,
    ) -> None:
        self.provider = provider
        self.model_policy = model_policy or ModelPolicy()
        self.context = context or QuestionContext()

    def understand(self, question: str) -> tuple[QuestionUnderstanding, ModelDecision | None, bool]:
        deterministic = understand_question(question, context=self.context)
        if self.provider is None:
            return deterministic, None, True
        if not _requires_model_understanding(deterministic):
            # High-confidence deterministic semantics avoid a network call. This
            # is an intentional fast path, not a provider failure/fallback.
            return deterministic, None, False
        decision = self.model_policy.select(
            ModelStage.QUESTION_UNDERSTANDING,
            ambiguity=any(item.resolved_to is None for item in deterministic.ambiguity),
            facet_count=len(deterministic.facets),
            mixed_language=deterministic.language_profile == "mixed",
            high_stakes=deterministic.safety_class in {"medication", "high_stakes"},
        )
        try:
            content = self.provider.generate_json(
                system_prompt=QUESTION_INTELLIGENCE_SYSTEM_PROMPT,
                user_prompt=json.dumps({"question": question}, ensure_ascii=False, separators=(",", ":")),
                decision=decision,
            )
            parsed = parse_question_understanding(content, question=question, context=self.context)
            return _augment_with_deterministic(parsed, deterministic), decision, False
        except (ValueError, TypeError, json.JSONDecodeError, RuntimeError):
            return deterministic, decision, True


def parse_question_understanding(
    content: str,
    *,
    question: str,
    context: QuestionContext | None = None,
) -> QuestionUnderstanding:
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("question intelligence output must be an object")
    if payload.get("schema_version") != QUESTION_UNDERSTANDING_SCHEMA_VERSION:
        raise ValueError("unsupported question intelligence schema")
    base = understand_question(question, context=context or QuestionContext())
    domain = _enum_value(payload.get("domain"), {item.value for item in QuestionDomain}, base.domain)
    intent = _enum_value(payload.get("intent"), {item.value for item in QuestionIntent}, base.intent)
    freshness = _enum_value(payload.get("freshness"), {item.value for item in FreshnessClass}, base.freshness)
    confidence = _enum_value(payload.get("confidence_class"), {item.value for item in ConfidenceClass}, base.confidence_class)
    safety = _enum_value(payload.get("safety_class"), {item.value for item in SafetyClass}, base.safety_class)
    model_facets = tuple(value for value in _strings(payload.get("facets"), 16) if value in FACET_BY_NAME)
    facets = _canonical_facets(model_facets or base.facets)
    entities = _parse_model_entities(payload.get("entities"), question=question, base=base.entities)
    geography_raw = payload.get("geography") if isinstance(payload.get("geography"), dict) else {}
    geography = Geography(
        country_code=_optional_text(geography_raw.get("country_code")) or base.geography.country_code,
        label=_optional_text(geography_raw.get("label")) or base.geography.label,
        explicit=bool(geography_raw.get("explicit", base.geography.explicit)),
        source="llm" if geography_raw else base.geography.source,
    )
    return replace(
        base,
        domain=domain,
        subdomain=_optional_text(payload.get("subdomain")) or base.subdomain,
        intent=intent,
        entities=entities,
        facets=facets,
        geography=geography,
        freshness=freshness,
        archive_specific=_bool(payload.get("archive_specific"), base.archive_specific),
        scientific_evidence_needed=_bool(payload.get("scientific_evidence_needed"), base.scientific_evidence_needed),
        current_information_needed=_bool(payload.get("current_information_needed"), base.current_information_needed),
        confidence_class=confidence,
        safety_class=safety,
    )


def _augment_with_deterministic(model: QuestionUnderstanding, deterministic: QuestionUnderstanding) -> QuestionUnderstanding:
    facets = _canonical_facets(tuple(dict.fromkeys((*model.facets, *deterministic.facets))))
    entities = model.entities or deterministic.entities
    ambiguity = model.ambiguity or deterministic.ambiguity
    return replace(
        model,
        entities=entities,
        facets=facets,
        ambiguity=ambiguity,
        archive_specific=model.archive_specific or deterministic.archive_specific,
        scientific_evidence_needed=model.scientific_evidence_needed or deterministic.scientific_evidence_needed,
        current_information_needed=model.current_information_needed or deterministic.current_information_needed,
    )



def _parse_model_entities(value: Any, *, question: str, base: tuple[EntityMention, ...]) -> tuple[EntityMention, ...]:
    if not isinstance(value, list):
        return base
    normalized_question = normalize_text(question).casefold()
    out = list(base)
    existing = {item.canonical_id for item in out}
    for raw in value[:10]:
        if not isinstance(raw, dict):
            continue
        text = _optional_text(raw.get("text"))
        canonical = _optional_text(raw.get("canonical")) or text
        entity_type = _optional_text(raw.get("type")) or "dental_term"
        if not text or not canonical:
            continue
        normalized_text = normalize_text(text).casefold()
        # The model may normalize terminology, but may not introduce an entity that
        # is absent from the actual user utterance. This blocks answer-fact leakage.
        if normalized_text not in normalized_question:
            continue
        cid = normalize_text(canonical).casefold().replace(" ", "_")[:80]
        if not cid or cid in existing:
            continue
        existing.add(cid)
        out.append(EntityMention(
            text=text, canonical_id=cid, canonical_label=canonical, entity_type=entity_type,
            variants=(text, canonical), inferred=False, confidence=0.72,
        ))
    return tuple(out)



def _requires_model_understanding(value: QuestionUnderstanding) -> bool:
    unresolved = any(item.resolved_to is None for item in value.ambiguity)
    return bool(
        unresolved
        or value.confidence_class == ConfidenceClass.LOW
        or value.safety_class in {SafetyClass.MEDICATION, SafetyClass.HIGH_STAKES}
        or len(value.facets) >= 3
        # Mixed script by itself is not ambiguity. Escalate only when the
        # deterministic understanding is actually low-confidence; otherwise
        # common Persian+English dental terms (e.max, RCT, etc.) stay on the
        # deterministic zero-QI-call path.
        or (value.language_profile == "mixed" and value.confidence_class == ConfidenceClass.LOW)
    )


def _canonical_facets(values: tuple[str, ...]) -> tuple[str, ...]:
    out = [value for value in values if value in FACET_BY_NAME]
    # Prevalence questions often prompt a model to redundantly emit both
    # prevalence and frequency. Keep the more specific requested-fact facet.
    if "prevalence" in out and "frequency" in out:
        out = [value for value in out if value != "frequency"]
    return tuple(dict.fromkeys(out))


def _enum_value(value: Any, allowed: set[str], default: str) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    return text if text in allowed else str(default)


def _strings(value: Any, limit: int) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        text = item.strip().casefold().replace("-", "_").replace(" ", "_")
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text[:120] or None


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


__all__ = [
    "QUESTION_INTELLIGENCE_SYSTEM_PROMPT", "IntelligenceModelProvider",
    "QuestionIntelligenceEngine", "parse_question_understanding",
]
