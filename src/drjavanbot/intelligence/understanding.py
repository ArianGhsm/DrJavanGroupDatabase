from __future__ import annotations

from dataclasses import dataclass
import re

from drjavanbot.normalization import normalize_text, tokenize
from .concepts import DentalConceptResolver
from .facets import detect_facets, facet_spec
from .models import (
    ConfidenceClass,
    EntityMention,
    FreshnessClass,
    Geography,
    LanguageProfile,
    QuestionConstraints,
    QuestionDomain,
    QuestionIntent,
    QuestionUnderstanding,
    SafetyClass,
    SourceType,
)


@dataclass(frozen=True, slots=True)
class QuestionContext:
    default_domain: str = QuestionDomain.DENTISTRY
    default_profession: str | None = "dentistry"
    default_country_code: str | None = "IR"
    default_country_label: str | None = "Iran"
    recent_entities: tuple[EntityMention, ...] = ()
    recent_subdomain: str | None = None
    recent_geography: Geography = Geography()


_ARCHIVE_MARKERS = (
    "تو گروه", "در گروه", "گروه درباره", "گروه چی", "نظر گروه", "بچه ها تو گروه",
    "بچه های گروه", "پیام های گروه", "پیام‌های گروه", "آرشیو", "دکتر جوان", "drjavan",
)
_SCIENCE_MARKERS = (
    "مقاله", "مقالات", "مطالعه", "شواهد علمی", "علمی", "گایدلاین", "guideline",
    "paper", "papers", "study", "studies", "evidence", "literature", "pubmed",
)
_CURRENT_MARKERS = (
    "الان", "فعلا", "فعلی", "امروز", "جدید", "آخرین", "به روز", "به‌روز", "current",
    "currently", "today", "latest", "recent", "now",
)
_IRAN_MARKERS = ("ایران", "iran", "تهران", "tehran")
_NEW_GRAD_MARKERS = ("تازه فارغ", "فارغ التحصیل جدید", "تازه کار", "تازه‌کار", "new graduate", "newly graduated")
_MEDICATION_MARKERS = ("دوز", "dose", "dosage", "دارو", "آنتی بیوتیک", "antibiotic", "analgesic")
_CLINICAL_MARKERS = (
    "درمان", "تشخیص", "بیمار", "دارو", "جراحی", "اندیکاسیون", "کنتراندیکاسیون",
    "treatment", "diagnosis", "patient", "surgery", "indication", "contraindication",
)


def understand_question(
    question: str,
    *,
    context: QuestionContext | None = None,
    resolver: DentalConceptResolver | None = None,
) -> QuestionUnderstanding:
    context = context or QuestionContext()
    resolver = resolver or DentalConceptResolver.load_default()
    normalized = normalize_text(question)
    language = _language_profile(question)
    concepts = resolver.resolve(question)
    ambiguities = resolver.ambiguities(question)
    facets = detect_facets(question)

    archive_specific = _contains_any(normalized, _ARCHIVE_MARKERS)
    if archive_specific and facets == ("definition",) and not any(value in normalized for value in ("تعریف", "define", "definition")):
        facets = ()
    explicit_science = _contains_any(normalized, _SCIENCE_MARKERS)
    explicit_current = _contains_any(normalized, _CURRENT_MARKERS)
    current_sensitive = any(
        (spec := facet_spec(facet)) is not None and spec.freshness_sensitivity == FreshnessClass.CURRENT
        for facet in facets
    )
    current_needed = (
        (explicit_current and "guideline" not in facets)
        or (current_sensitive and not archive_specific)
        or "regulation" in facets
    )

    if _explicit_non_dental_context(normalized):
        domain, subdomain = QuestionDomain.GENERAL, None
    else:
        domain, subdomain = _domain_and_subdomain(concepts, facets, context)
    entities = _entities(concepts)
    constraints = _constraints(normalized, entities, facets, context)
    entities = _augment_context_entities(entities, constraints, facets, context)
    geography = _geography(normalized, current_needed, facets, context)

    scientific_needed = _scientific_need(
        domain=domain,
        facets=facets,
        archive_specific=archive_specific,
        explicit_science=explicit_science,
        current_needed=current_needed,
    )
    freshness = _freshness(facets, current_needed, explicit_current)
    intent = _intent(facets, archive_specific, scientific_needed, current_needed)
    source_preferences = _source_preferences(normalized, archive_specific, explicit_science, current_needed)
    safety = _safety(normalized, facets)
    confidence = _confidence(normalized, facets, concepts, ambiguities)

    return QuestionUnderstanding(
        normalized_question=normalized,
        language_profile=language,
        domain=domain,
        subdomain=subdomain,
        intent=intent,
        entities=entities,
        facets=facets,
        constraints=constraints,
        geography=geography,
        freshness=freshness,
        source_preferences=source_preferences,
        archive_specific=archive_specific,
        scientific_evidence_needed=scientific_needed,
        current_information_needed=current_needed,
        ambiguity=ambiguities,
        confidence_class=confidence,
        safety_class=safety,
    )


def _language_profile(raw: str) -> str:
    has_fa = bool(re.search(r"[\u0600-\u06ff]", raw))
    has_en = bool(re.search(r"[A-Za-z]", raw))
    if has_fa and has_en:
        return LanguageProfile.MIXED
    if has_fa:
        return LanguageProfile.PERSIAN
    if has_en:
        return LanguageProfile.ENGLISH
    return LanguageProfile.UNKNOWN


def _domain_and_subdomain(concepts, facets: tuple[str, ...], context: QuestionContext) -> tuple[str, str | None]:
    subdomains = [item.subdomain for item in concepts if item.subdomain]
    if concepts:
        return QuestionDomain.DENTISTRY, subdomains[0] if subdomains else None
    if any(facet in {"salary", "career", "cost"} for facet in facets):
        if context.default_domain == QuestionDomain.DENTISTRY:
            return QuestionDomain.DENTISTRY, "career_economics"
        return QuestionDomain.CAREER_ECONOMICS, "career_economics"
    if "regulation" in facets:
        return QuestionDomain.REGULATION, "regulation"
    if facets and context.recent_subdomain:
        return QuestionDomain.DENTISTRY, context.recent_subdomain
    if facets and context.default_domain:
        return str(context.default_domain), None
    return QuestionDomain.UNKNOWN, None


def _entities(concepts) -> tuple[EntityMention, ...]:
    return tuple(
        EntityMention(
            text=item.matched_text,
            canonical_id=item.concept_id,
            canonical_label=item.canonical,
            entity_type=item.entity_type,
            variants=item.variants,
            confidence=item.confidence,
            subdomain=item.subdomain,
        )
        for item in concepts
    )


def _augment_context_entities(
    entities: tuple[EntityMention, ...],
    constraints: QuestionConstraints,
    facets: tuple[str, ...],
    context: QuestionContext,
) -> tuple[EntityMention, ...]:
    out = list(entities)
    ids = {item.canonical_id for item in out}
    if any(facet in {"salary", "career"} for facet in facets) and context.default_profession and "dentistry" not in ids:
        out.append(EntityMention(
            text=context.default_profession,
            canonical_id="dentistry",
            canonical_label="dentistry",
            entity_type="profession",
            variants=("dentistry", "dentist", "دندانپزشکی", "دندانپزشک"),
            inferred=True,
            confidence=0.75,
        ))
    if constraints.career_stage and "new_graduate" not in ids:
        out.append(EntityMention(
            text=constraints.career_stage[0],
            canonical_id="new_graduate",
            canonical_label="new graduate",
            entity_type="career_stage",
            variants=("new graduate", "تازه فارغ التحصیل", "تازه کار"),
            inferred=False,
            confidence=0.95,
            subdomain="career_economics",
        ))
    # Recent conversation semantics may resolve an omitted subject in a follow-up,
    # but are explicitly marked inferred and are never factual evidence.
    explicit_topic = any(not item.inferred and item.entity_type not in {"career_stage"} for item in out)
    if not explicit_topic and facets and context.recent_entities:
        for item in context.recent_entities[:4]:
            if item.canonical_id in {value.canonical_id for value in out}:
                continue
            out.append(EntityMention(
                text=item.canonical_label, canonical_id=item.canonical_id, canonical_label=item.canonical_label,
                entity_type=item.entity_type, variants=item.variants, inferred=True,
                confidence=min(float(item.confidence), 0.72), subdomain=item.subdomain,
            ))
    return tuple(out)


def _constraints(
    normalized: str,
    entities: tuple[EntityMention, ...],
    facets: tuple[str, ...],
    context: QuestionContext,
) -> QuestionConstraints:
    population: list[str] = []
    if any(value in normalized for value in ("کودک", "بچه", "نوجوان", "pediatric", "child", "adolescent")):
        population.append("pediatric")
    career_stage = tuple(value for value in _NEW_GRAD_MARKERS if value in normalized)[:1]
    profession = tuple(
        item.canonical_label for item in entities if item.entity_type == "profession"
    )
    if not profession and any(facet in {"salary", "career"} for facet in facets) and context.default_profession:
        profession = (context.default_profession,)
    temporal = tuple(value for value in _CURRENT_MARKERS if normalize_text(value) in normalized)[:3]
    return QuestionConstraints(
        population=tuple(population),
        temporal=temporal,
        comparison_targets=_comparison_targets(normalized),
        career_stage=career_stage,
        profession=profession,
    )


def _comparison_targets(normalized: str) -> tuple[str, ...]:
    for sep in (" vs ", " versus ", " یا "):
        if sep in f" {normalized} ":
            parts = [part.strip() for part in normalized.split(sep.strip()) if part.strip()]
            if len(parts) >= 2:
                return tuple(parts[:2])
    return ()


def _geography(normalized: str, current_needed: bool, facets: tuple[str, ...], context: QuestionContext) -> Geography:
    if any(normalize_text(value) in normalized for value in _IRAN_MARKERS):
        return Geography(country_code="IR", label="Iran", explicit=True, source="question")
    if current_needed and context.recent_geography.country_code:
        return Geography(
            country_code=context.recent_geography.country_code,
            label=context.recent_geography.label, explicit=False, source="conversation",
        )
    if current_needed and context.default_country_code:
        return Geography(
            country_code=context.default_country_code,
            label=context.default_country_label,
            explicit=False,
            source="product_default",
        )
    return Geography()


def _scientific_need(*, domain: str, facets: tuple[str, ...], archive_specific: bool, explicit_science: bool, current_needed: bool) -> bool:
    if explicit_science:
        return True
    if current_needed and not archive_specific:
        return False
    if domain != QuestionDomain.DENTISTRY:
        return False
    scientific_facets = {
        "definition", "classification", "prevalence", "frequency", "epidemiology", "age", "sex",
        "location", "distribution", "etiology", "cause", "risk_factor", "signs", "symptoms", "diagnosis",
        "differential_diagnosis", "radiographic_features", "histopathology", "indication", "contraindication",
        "treatment", "technique", "method", "dosage", "timing", "duration", "comparison", "prognosis",
        "recurrence", "complication", "follow_up", "guideline", "clinical_decision",
    }
    return bool(set(facets) & scientific_facets) and not archive_specific


def _freshness(facets: tuple[str, ...], current_needed: bool, explicit_current: bool) -> str:
    # "new/latest guideline" is a recent scientific-version question, not a
    # market/current-web claim. Regulation remains current/official.
    if "guideline" in facets and not current_needed:
        return FreshnessClass.RECENT
    if explicit_current or current_needed:
        return FreshnessClass.CURRENT
    if any((spec := facet_spec(facet)) is not None and spec.freshness_sensitivity == FreshnessClass.RECENT for facet in facets):
        return FreshnessClass.RECENT
    if facets:
        return FreshnessClass.EVERGREEN
    return FreshnessClass.UNSPECIFIED


def _intent(facets: tuple[str, ...], archive_specific: bool, scientific_needed: bool, current_needed: bool) -> str:
    if archive_specific and (scientific_needed or current_needed):
        return QuestionIntent.HYBRID
    if archive_specific:
        return QuestionIntent.ARCHIVE_OPINION
    if current_needed and any(facet in {"salary", "career", "cost"} for facet in facets):
        return QuestionIntent.CAREER
    if current_needed:
        return QuestionIntent.CURRENT_INFORMATION
    if "regulation" in facets:
        return QuestionIntent.REGULATORY
    for facet, intent in (
        ("definition", QuestionIntent.DEFINITION),
        ("classification", QuestionIntent.CLASSIFICATION),
        ("differential_diagnosis", QuestionIntent.DIFFERENTIAL_DIAGNOSIS),
        ("diagnosis", QuestionIntent.DIAGNOSIS),
        ("treatment", QuestionIntent.TREATMENT),
        ("technique", QuestionIntent.TECHNIQUE),
        ("method", QuestionIntent.TECHNIQUE),
        ("comparison", QuestionIntent.COMPARISON),
        ("recommendation", QuestionIntent.RECOMMENDATION),
    ):
        if facet in facets:
            return intent
    return QuestionIntent.FACTUAL if facets else QuestionIntent.UNKNOWN


def _source_preferences(normalized: str, archive_specific: bool, science: bool, current: bool) -> tuple[str, ...]:
    out: list[str] = []
    if archive_specific:
        out.append(SourceType.ARCHIVE)
    if science:
        out.append(SourceType.SCIENTIFIC)
    if current:
        out.append(SourceType.CURRENT_WEB)
    if "رسمی" in normalized or "official" in normalized:
        out.append(SourceType.OFFICIAL)
    return tuple(dict.fromkeys(str(value) for value in out))


def _safety(normalized: str, facets: tuple[str, ...]) -> str:
    if _contains_any(normalized, _MEDICATION_MARKERS) or "dosage" in facets:
        return SafetyClass.MEDICATION
    if _contains_any(normalized, _CLINICAL_MARKERS) or any(facet in {"diagnosis", "treatment", "clinical_decision", "contraindication"} for facet in facets):
        return SafetyClass.CLINICAL
    return SafetyClass.GENERAL


def _confidence(normalized: str, facets: tuple[str, ...], concepts, ambiguities) -> str:
    unresolved = any(item.resolved_to is None for item in ambiguities)
    if unresolved:
        return ConfidenceClass.LOW
    if concepts and facets:
        return ConfidenceClass.HIGH
    if facets or concepts or len(tokenize(normalized)) >= 3:
        return ConfidenceClass.MEDIUM
    return ConfidenceClass.LOW


def _explicit_non_dental_context(normalized: str) -> bool:
    return any(value in normalized for value in (
        "حقوق بشر", "حقوق مدنی", "human rights", "legal rights",
        "cost function", "تابع هزینه", "loss function",
    ))


def _contains_any(normalized: str, values) -> bool:
    return any(normalize_text(value) in normalized for value in values)


__all__ = ["QuestionContext", "understand_question"]
