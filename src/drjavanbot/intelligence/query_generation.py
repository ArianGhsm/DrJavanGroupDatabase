from __future__ import annotations

from datetime import datetime

from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_query
from .facets import facet_spec
from .models import QuestionUnderstanding, RetrievalQuery, RetrievalRequest, SourceRoute, SourceType

MAX_QUERIES_PER_SOURCE = 18


def generate_retrieval_requests(understanding: QuestionUnderstanding, route: SourceRoute, *, top_k: int = 20) -> tuple[RetrievalRequest, ...]:
    requests: list[RetrievalRequest] = []
    for selection in route.selected_sources:
        if selection.source_type == SourceType.NONE:
            continue
        queries = _queries_for_source(understanding, selection.source_type)
        requests.append(RetrievalRequest(
            source_type=selection.source_type, normalized_question=understanding.normalized_question,
            queries=queries[:MAX_QUERIES_PER_SOURCE], entity_ids=tuple(item.canonical_id for item in understanding.entities),
            facets=understanding.facets, freshness=selection.freshness_requirement, geography=understanding.geography,
            top_k=max(1, min(int(top_k), 100)),
        ))
    return tuple(requests)


def _queries_for_source(understanding: QuestionUnderstanding, source_type: str) -> tuple[RetrievalQuery, ...]:
    if source_type == SourceType.ARCHIVE:
        return _archive_queries(understanding)
    if source_type == SourceType.SCIENTIFIC:
        return _scientific_queries(understanding)
    if source_type == SourceType.CURRENT_WEB:
        return _current_queries(understanding)
    if source_type == SourceType.OFFICIAL:
        return _official_queries(understanding)
    if source_type == SourceType.DENTAL_KNOWLEDGE:
        return _knowledge_queries(understanding)
    return ()


def _topic_terms(understanding: QuestionUnderstanding) -> tuple[str, ...]:
    values: list[str] = []
    for entity in understanding.entities:
        if entity.entity_type == "career_stage":
            continue
        for value in (entity.canonical_label, entity.text):
            text = " ".join(str(value or "").split())
            if text and text not in values:
                values.append(text)
    if not values:
        fallback = informative_query(understanding.normalized_question)
        if fallback:
            values.append(fallback)
    return tuple(values[:4])


def _scientific_queries(understanding: QuestionUnderstanding) -> tuple[RetrievalQuery, ...]:
    english_terms: list[str] = []
    for entity in understanding.entities:
        if entity.entity_type == "career_stage": continue
        candidate = next((str(value) for value in (entity.canonical_label, *entity.variants) if value and str(value).isascii()), None)
        if candidate and candidate.casefold() not in {value.casefold() for value in english_terms}: english_terms.append(candidate)
    topic_terms = tuple(english_terms) or _topic_terms(understanding)
    # Prefer the most specific canonical concept; related entities become separate query families.
    topic = topic_terms[0] if topic_terms else ""
    out: list[RetrievalQuery] = []
    if topic:
        out.append(RetrievalQuery(topic, "scientific_topic", "topic", 100, True, True))
    facet_terms = {
        "prevalence": "prevalence frequency epidemiology",
        "frequency": "frequency prevalence",
        "epidemiology": "epidemiology prevalence incidence",
        "classification": "classification taxonomy",
        "definition": "definition terminology",
        "diagnosis": "diagnosis diagnostic criteria",
        "differential_diagnosis": "differential diagnosis",
        "radiographic_features": "radiographic features imaging",
        "histopathology": "histopathology histology",
        "treatment": "treatment management systematic review",
        "clinical_decision": "clinical guideline systematic review",
        "guideline": "guideline consensus practice guideline",
        "recurrence": "recurrence rate",
        "prognosis": "prognosis outcome",
        "complication": "complications adverse outcomes",
        "dosage": "dose dosage dentistry guideline",
        "contraindication": "contraindications guideline",
        "indication": "indications guideline",
        "age": "age distribution",
        "sex": "sex distribution",
        "location": "anatomic distribution",
    }
    for facet in understanding.facets[:5]:
        term = facet_terms.get(facet, facet.replace("_", " "))
        if topic:
            out.append(RetrievalQuery(f"{topic} {term}", f"scientific_{facet}", "intersection", 96))
    if set(understanding.facets) & {"prevalence", "frequency", "epidemiology", "treatment", "comparison", "clinical_decision"} and topic:
        out.append(RetrievalQuery(f"{topic} systematic review", "scientific_review", "semantic", 94))
    # Preserve a canonical English-style query even when the user wrote Persian.
    for entity in understanding.entities[:3]:
        english = next((v for v in (entity.canonical_label, *entity.variants) if v and str(v).isascii()), None)
        if english:
            out.append(RetrievalQuery(str(english), f"scientific_entity_{entity.canonical_id}", "entity", 92, True, False))
    return _dedupe(out)


def _current_queries(understanding: QuestionUnderstanding) -> tuple[RetrievalQuery, ...]:
    year = datetime.now().year
    solar_year = 1405 if year == 2026 else None
    geography = understanding.geography.label or understanding.geography.country_code or ""
    topic = " ".join(_topic_terms(understanding)[:2])
    out: list[RetrievalQuery] = []
    base = " ".join(value for value in (understanding.normalized_question, geography, str(year)) if value)
    if base:
        out.append(RetrievalQuery(base, "current_local_language", "semantic", 100, True, False))
    if solar_year and geography.casefold() in {"iran", "ir"}:
        out.append(RetrievalQuery(f"{understanding.normalized_question} ایران {solar_year}", "current_solar_year", "constraint", 99))
    facets = set(understanding.facets)
    if "salary" in facets:
        for terms in (
            "حقوق دندانپزشک تازه فارغ التحصیل ایران",
            "درآمد دندانپزشک تازه کار ایران",
            "استخدام دندانپزشک حقوق درصدی کلینیک ایران",
            "dentist salary new graduate Iran",
        ):
            out.append(RetrievalQuery(f"{terms} {year}", "current_salary", "intersection", 98))
    if "cost" in facets and topic:
        out.append(RetrievalQuery(f"قیمت {topic} ایران {year}", "current_price_fa", "intersection", 97))
        out.append(RetrievalQuery(f"{topic} price Iran {year}", "current_price_en", "intersection", 94))
    if "career" in facets:
        out.append(RetrievalQuery(f"استخدام دندانپزشک تازه کار ایران {year}", "current_jobs", "intersection", 95))
    if "regulation" in facets:
        out.append(RetrievalQuery(f"قانون مقررات دندانپزشکی ایران {year}", "current_regulation", "intersection", 97))
    return _dedupe(out)


def _official_queries(understanding: QuestionUnderstanding) -> tuple[RetrievalQuery, ...]:
    year = datetime.now().year
    topic = " ".join(_topic_terms(understanding)[:2])
    geography = understanding.geography.label or ""
    out: list[RetrievalQuery] = []
    facets = set(understanding.facets)
    if "regulation" in facets:
        out.extend((
            RetrievalQuery(f"{topic} مقررات مجوز {geography} {year}", "official_regulation_fa", "constraint", 100),
            RetrievalQuery(f"{topic} regulation license {geography} {year}", "official_regulation_en", "constraint", 96),
        ))
    elif "guideline" in facets:
        out.append(RetrievalQuery(f"{topic} guideline {year}", "official_guideline", "intersection", 98))
    else:
        out.append(RetrievalQuery(f"{understanding.normalized_question} {geography} {year}", "official_current", "semantic", 92))
    return _dedupe(out)


def _knowledge_queries(understanding: QuestionUnderstanding) -> tuple[RetrievalQuery, ...]:
    topic = " ".join(_topic_terms(understanding)[:2])
    out = [RetrievalQuery(topic, "knowledge_topic", "topic", 100, True, True)] if topic else []
    for facet in understanding.facets[:4]:
        if topic:
            out.append(RetrievalQuery(f"{topic} {facet.replace('_',' ')}", f"knowledge_{facet}", "intersection", 95))
    return _dedupe(out)


def _archive_queries(understanding: QuestionUnderstanding) -> tuple[RetrievalQuery, ...]:
    out: list[RetrievalQuery] = []
    seen_topic_terms: set[str] = set()
    for index, entity in enumerate(understanding.entities[:4]):
        if entity.inferred and entity.entity_type in {"profession", "career_stage"}:
            continue
        # Words such as "brand/product" describe the requested answer shape;
        # they are not dental topic anchors.  Treating an LLM-extracted generic
        # "brand" entity as mandatory made otherwise relevant composite
        # discussions fail the co-location gate unless they repeated that exact
        # word.  Named products remain anchors; only generic class labels move to
        # the facet family below.
        if _is_generic_archive_filter_entity(entity):
            continue
        variants = tuple(dict.fromkeys(normalize_text(value) for value in (entity.canonical_label, entity.text, *entity.variants) if normalize_text(value)))
        normalized_variants = {value.casefold() for value in variants}
        # QI may return a normalized material entity in addition to the
        # deterministic ontology entity (for example composite and
        # dental_composite). They are aliases, not two independently required
        # topics. Requiring both made Persian discussions fail retrieval.
        if seen_topic_terms & normalized_variants:
            continue
        seen_topic_terms.update(normalized_variants)
        for variant in variants[:4]:
            out.append(RetrievalQuery(variant, f"topic_{index}_{entity.canonical_id}", "topic", 100 - index, True, True))
    if not any(item.anchor for item in out):
        topical = informative_query(understanding.normalized_question)
        if topical:
            out.append(RetrievalQuery(topical, "topic", "topic", 100, True, True))
    topic_seed = next((item.text for item in out if item.anchor), "")
    for facet in understanding.facets[:5]:
        spec = facet_spec(facet)
        if spec is None:
            continue
        for marker in spec.evidence_markers[:3]:
            out.append(RetrievalQuery(marker, f"facet_{facet}", "facet", 88))
        if topic_seed and spec.evidence_markers:
            out.append(RetrievalQuery(f"{topic_seed} {spec.evidence_markers[0]}", f"intersection_{facet}", "intersection", 94, True, False))
    return _dedupe(out)


def _is_generic_archive_filter_entity(entity) -> bool:
    if str(entity.entity_type).casefold() not in {"product", "brand"}:
        return False
    generic = {"brand", "product", "برند", "مارک", "محصول"}
    values = {
        normalize_text(value).casefold()
        for value in (entity.text, entity.canonical_id, entity.canonical_label)
        if normalize_text(value)
    }
    return bool(values) and values <= generic


def _dedupe(values: list[RetrievalQuery]) -> tuple[RetrievalQuery, ...]:
    out: list[RetrievalQuery] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        normalized = normalize_text(item.text)
        key = (item.family, normalized)
        if not normalized or key in seen:
            continue
        seen.add(key); out.append(item)
    return tuple(out)


__all__ = ["MAX_QUERIES_PER_SOURCE", "generate_retrieval_requests"]
