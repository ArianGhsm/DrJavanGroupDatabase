from __future__ import annotations

import re
from typing import Any, Iterable

from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_query, informative_tokens
from drjavanbot.intelligence.concepts import DentalConceptResolver, concept_variant_groups
from .query_model import (
    QUERY_MODEL_VERSION,
    MAX_FINAL_FAMILIES,
    MAX_INITIAL_FAMILIES,
    MAX_QUERIES_PER_FAMILY,
    MAX_TOTAL_QUERIES,
    EvidencePattern,
    FamilyPurpose,
    RetrievalDepth,
    RetrievalPolicy,
    SearchFamily,
    SearchPlan,
)
from .search_policy import (
    comparison_targets,
    derive_retrieval_policy,
    facet_query_terms,
    infer_question_facets,
    sanitize_hint,
)
from .validation import ModelOutputError, parse_json_object

PLANNER_VERSION = f"semantic-search-plan-v3-{QUERY_MODEL_VERSION}"
_MAX_MODEL_FAMILIES = 8
_MAX_PARSED_INITIAL_QUERIES = MAX_INITIAL_FAMILIES * MAX_QUERIES_PER_FAMILY
_REPEAT_RE = re.compile(r"(.)\1{2,}")
_GENERIC_TOPIC_QUALIFIERS = frozenset(
    normalize_text(value)
    for value in ("برند", "مارک", "محصول", "brand", "product", "متریال", "material")
)


def parse_search_plan(content: str, *, question: str) -> SearchPlan:
    return plan_from_payload(parse_json_object(content), question=question)


def parse_refinement_families(content: str, *, question: str = "") -> tuple[SearchFamily, ...]:
    payload = parse_json_object(content)
    return _parse_families(
        payload.get("query_families"),
        max_families=4,
        total_limit=10,
        question=question,
    )


def deterministic_fallback_plan(question: str) -> SearchPlan:
    """Fail-soft local understanding using only language features in the question."""
    normalized = normalize_text(question)
    topical = informative_query(normalized)
    tokens = informative_tokens(normalized)
    searchable = bool(topical)
    facets = infer_question_facets(question)
    topic_anchors = _deterministic_topic_anchors(question, facets)
    topic_anchor_groups = _deterministic_topic_anchor_groups(question, facets)
    typo_hints = _mechanical_typo_hints(question)

    families: list[SearchFamily] = []
    topic_query = " ".join(topic_anchors) if topic_anchors else topical
    if topic_query:
        families.append(SearchFamily(
            "topic",
            (topic_query,),
            purpose=FamilyPurpose.TOPIC,
            priority=100,
            anchor=True,
        ))
    families.extend(_generic_aspect_families(question, existing=families, facets=facets))
    intersections = _deterministic_intersections(topic_anchors, facets, question=question)
    if intersections:
        families.append(SearchFamily(
            "intersection",
            intersections,
            purpose=FamilyPurpose.INTERSECTION,
            priority=88,
            anchor=True,
        ))
    families = list(_select_initial_families(_dedupe_families(families), facets))
    policy = derive_retrieval_policy(question, facets=facets, family_count=len(families))
    required = _required_aspects(searchable, facets)
    intent = _intent_from_facets(facets, searchable=searchable)

    if searchable and not topic_anchor_groups:
        topic_anchor_groups = _deterministic_topic_anchor_groups(question, facets)

    return SearchPlan(
        searchable=searchable,
        intent=intent,
        core_concepts=topic_anchors or tokens[:8],
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=tuple(families),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=str(policy.expected_evidence_pattern) != str(EvidencePattern.SINGLE_MESSAGE),
        required_aspects=required,
        schema_version=QUERY_MODEL_VERSION,
        normalized_intent=intent,
        topic_anchors=topic_anchors,
        topic_anchor_groups=topic_anchor_groups,
        answer_facets=facets,
        population_constraints=("pediatric_population",) if "pediatric_population" in facets else (),
        comparison_targets=comparison_targets(question),
        typo_hints=typo_hints,
        intersection_queries=intersections,
        expected_evidence_pattern=str(policy.expected_evidence_pattern),
        retrieval_policy=policy,
    )


def infer_question_aspects(question: str) -> tuple[str, ...]:
    """Backward-compatible alias for the typed deterministic facet detector."""
    return infer_question_facets(question)


def plan_requires_deep_retrieval(plan: SearchPlan) -> bool:
    """True when superficial lexical strength must not short-circuit facet coverage."""
    if str(plan.retrieval_policy.depth) == str(RetrievalDepth.DEEP):
        return True
    demanding = {
        "timing_age", "comparison", "recommendation", "cause_reason", "method_how",
        "quantity", "dosage", "indication", "complication", "prognosis",
        "prevalence", "frequency", "epidemiology", "diagnosis", "differential_diagnosis",
        "treatment", "contraindication", "recurrence", "follow_up", "salary", "cost", "career", "regulation",
    }
    return bool(set(plan.required_aspects) & demanding)


def observed_vocabulary(candidates: Iterable[Any], *, question: str, limit: int = 36) -> tuple[str, ...]:
    from drjavanbot.search.terms import distinctive_terms

    texts: list[str | None] = []
    for candidate in list(candidates)[:18]:
        message = getattr(candidate, "message", None)
        texts.append(getattr(message, "text_normalized", None) or getattr(message, "text_raw", None))
        for context in getattr(candidate, "context", ())[:6]:
            texts.append(getattr(context, "text_normalized", None) or getattr(context, "text_raw", None))
        texts.extend(getattr(candidate, "matched_terms", ()))
    return distinctive_terms(texts, exclude=(question,), limit=limit)


def plan_from_payload(payload: dict[str, Any], *, question: str) -> SearchPlan:
    """Parse both the typed v1 schema and the legacy SearchPlan payload fail-soft."""
    schema_version = payload.get("schema_version")
    if schema_version is not None and schema_version != QUERY_MODEL_VERSION:
        raise ModelOutputError("search planner schema_version is unsupported")

    searchable = payload.get("searchable", True)
    if not isinstance(searchable, bool):
        raise ModelOutputError("search planner searchable must be boolean")

    normalized_intent = _bounded_text(
        payload.get("normalized_intent", payload.get("intent")),
        default="archive_lookup",
        max_len=80,
    )
    intent = _bounded_text(payload.get("intent", normalized_intent), default=normalized_intent, max_len=160)

    constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
    hints = payload.get("hints") if isinstance(payload.get("hints"), dict) else {}

    topic_anchors = _string_tuple(
        payload.get("topic_anchors", payload.get("core_concepts")),
        max_items=10,
        max_len=80,
        question=question,
    )
    core = _string_tuple(
        payload.get("core_concepts", topic_anchors),
        max_items=10,
        max_len=80,
        question=question,
    ) or topic_anchors
    topic_anchor_groups = _parse_topic_anchor_groups(payload.get("topic_anchor_groups"), question=question)
    aliases = _string_tuple(
        hints.get("aliases", payload.get("aliases")),
        max_items=14,
        max_len=80,
        question=question,
    )
    optional = _string_tuple(payload.get("optional_concepts"), max_items=10, max_len=80, question=question)
    entities = _string_tuple(payload.get("entity_types"), max_items=6, max_len=60, question=question)
    phrases = _string_tuple(payload.get("phrases"), max_items=8, max_len=100, question=question)
    excludes = _string_tuple(payload.get("exclude_terms"), max_items=8, max_len=60, question=question)
    low = _string_tuple(payload.get("low_information_terms"), max_items=10, max_len=40, question=question)

    model_facets = _string_tuple(
        payload.get("required_facets", payload.get("answer_facets", payload.get("required_aspects"))),
        max_items=12,
        max_len=60,
        question=question,
    )
    legacy_aspects = _string_tuple(payload.get("required_aspects"), max_items=12, max_len=60, question=question)
    inferred_facets = infer_question_facets(question)
    raw_facets = _unique_text((*model_facets, *legacy_aspects, *inferred_facets))[:12]
    facets = tuple(facet for facet in raw_facets if facet != "topic")
    required_seed = ("topic",) if searchable else ()
    required = _unique_text((*required_seed, *facets))[:12]

    population = _string_tuple(
        constraints.get("population", payload.get("population_constraints")),
        max_items=8,
        max_len=80,
        question=question,
    )
    condition = _string_tuple(
        constraints.get("condition", payload.get("condition_constraints")),
        max_items=8,
        max_len=80,
        question=question,
    )
    temporal = _string_tuple(
        constraints.get("temporal", payload.get("temporal_constraints")),
        max_items=8,
        max_len=80,
        question=question,
    )
    compare = _string_tuple(
        constraints.get("comparison_targets", payload.get("comparison_targets")),
        max_items=6,
        max_len=90,
        question=question,
    ) or comparison_targets(question)
    terminology = _string_tuple(
        hints.get("terminology", payload.get("terminology_hints")),
        max_items=14,
        max_len=90,
        question=question,
    )
    colloquial = _string_tuple(
        hints.get("colloquial", payload.get("colloquial_hints")),
        max_items=10,
        max_len=90,
        question=question,
    )
    typo_hints = _unique_text((
        *_string_tuple(hints.get("typos", payload.get("typo_hints")), max_items=10, max_len=90, question=question),
        *_mechanical_typo_hints(question),
    ))[:10]
    intersection_queries = _string_tuple(
        payload.get("intersection_queries"),
        max_items=8,
        max_len=120,
        question=question,
    )
    negative_hints = _string_tuple(
        payload.get("negative_hints"),
        max_items=8,
        max_len=80,
        question=question,
    )

    reply_context = payload.get("reply_context", True)
    if not isinstance(reply_context, bool):
        reply_context = True

    families = list(_parse_families(
        payload.get("query_families"),
        max_families=_MAX_MODEL_FAMILIES,
        total_limit=_MAX_PARSED_INITIAL_QUERIES,
        question=question,
    ))

    if searchable and not topic_anchors:
        topic_anchors = _deterministic_topic_anchors(question, facets)
        if not core:
            core = topic_anchors
    if searchable and topic_anchors and not any(_is_topic_family(family) for family in families):
        families.insert(0, SearchFamily(
            "topic",
            (" ".join(topic_anchors),),
            purpose=FamilyPurpose.TOPIC,
            priority=100,
            anchor=True,
        ))

    families.extend(_generic_aspect_families(question, existing=families, facets=facets))

    if aliases and not any(str(family.purpose) == str(FamilyPurpose.ALIAS) for family in families):
        families.append(SearchFamily("aliases", aliases[:4], purpose=FamilyPurpose.ALIAS, priority=72))
    if terminology and not any(str(family.purpose) in {str(FamilyPurpose.TERMINOLOGY), str(FamilyPurpose.STAGE)} for family in families):
        families.append(SearchFamily("terminology", terminology[:4], purpose=FamilyPurpose.TERMINOLOGY, priority=76))
    if typo_hints and not any("typo" in family.name.casefold() for family in families):
        families.append(SearchFamily("typo_variants", typo_hints[:4], purpose=FamilyPurpose.ALIAS, priority=65))

    generated_intersections = _deterministic_intersections(topic_anchors, facets, question=question)
    all_intersections = _unique_text((*intersection_queries, *generated_intersections))[:8]
    if all_intersections and not any(str(family.purpose) == str(FamilyPurpose.INTERSECTION) for family in families):
        families.append(SearchFamily(
            "intersection",
            all_intersections[:4],
            purpose=FamilyPurpose.INTERSECTION,
            priority=88,
            anchor=True,
        ))

    families = list(_select_initial_families(_dedupe_families(families), facets))

    if searchable and not families:
        fallback = deterministic_fallback_plan(question)
        families = list(fallback.query_families)
        if not core:
            core = fallback.core_concepts
        if not topic_anchors:
            topic_anchors = fallback.topic_anchors
        if not topic_anchor_groups:
            topic_anchor_groups = fallback.topic_anchor_groups
    if searchable and not families:
        searchable = False

    policy = derive_retrieval_policy(question, facets=facets, family_count=len(families))
    expected_pattern = _bounded_pattern(payload.get("expected_evidence_pattern"), default=str(policy.expected_evidence_pattern))
    # Deterministic policy owns cost/depth; model may only influence context shape
    # on non-deep plans. Deep plans cannot be downgraded by model output.
    if str(policy.depth) == str(RetrievalDepth.DEEP):
        expected_pattern = str(policy.expected_evidence_pattern)
    policy = RetrievalPolicy(
        depth=policy.depth,
        query_budget=policy.query_budget,
        family_budget=max(policy.family_budget, min(len(families), MAX_INITIAL_FAMILIES)),
        per_family_budget=policy.per_family_budget,
        rescue_allowed=policy.rescue_allowed,
        max_rescue_families=policy.max_rescue_families,
        expected_evidence_pattern=expected_pattern,
        stop_when_required_facets_covered=policy.stop_when_required_facets_covered,
        minimum_family_coverage=policy.minimum_family_coverage,
    ).bounded()

    if searchable and not topic_anchor_groups:
        topic_anchor_groups = _deterministic_topic_anchor_groups(question, facets)

    return SearchPlan(
        searchable=searchable,
        intent=intent,
        core_concepts=core,
        aliases=aliases,
        optional_concepts=optional,
        entity_types=entities,
        query_families=tuple(families),
        phrases=phrases,
        exclude_terms=excludes,
        low_information_terms=low,
        reply_context=reply_context or expected_pattern != str(EvidencePattern.SINGLE_MESSAGE),
        required_aspects=required,
        schema_version=QUERY_MODEL_VERSION,
        normalized_intent=normalized_intent,
        topic_anchors=topic_anchors,
        topic_anchor_groups=topic_anchor_groups,
        answer_facets=facets,
        population_constraints=population or (("pediatric_population",) if "pediatric_population" in facets else ()),
        condition_constraints=condition,
        temporal_constraints=temporal,
        comparison_targets=compare,
        terminology_hints=terminology,
        colloquial_hints=colloquial,
        typo_hints=typo_hints,
        intersection_queries=all_intersections,
        negative_hints=_unique_text((*negative_hints, *excludes))[:8],
        expected_evidence_pattern=expected_pattern,
        retrieval_policy=policy,
    )


def _generic_aspect_families(
    question: str,
    *,
    existing: Iterable[SearchFamily],
    facets: Iterable[str] | None = None,
) -> tuple[SearchFamily, ...]:
    required = tuple(facets if facets is not None else infer_question_facets(question))
    current = tuple(existing)
    out: list[SearchFamily] = []
    for facet in required:
        terms = facet_query_terms(facet)
        if not terms or any(_family_covers_facet(family, facet) for family in (*current, *out)):
            continue
        purpose = FamilyPurpose.POPULATION if facet.endswith("population") else FamilyPurpose.FACET
        name = "facet_population" if facet == "pediatric_population" else f"facet_{facet}"
        out.append(SearchFamily(
            name,
            terms[:MAX_QUERIES_PER_FAMILY],
            purpose=purpose,
            priority=94 if purpose == FamilyPurpose.POPULATION else 92,
            anchor=False,
        ))
    return tuple(out)


def _dedupe_families(families: Iterable[SearchFamily]) -> tuple[SearchFamily, ...]:
    out: list[SearchFamily] = []
    seen_names: set[str] = set()
    seen_queries: set[str] = set()
    for family in families:
        name = family.name.casefold().strip() or f"family-{len(out)+1}"
        queries: list[str] = []
        for query in family.queries:
            key = _near_duplicate_key(query)
            if not key or key in seen_queries:
                continue
            seen_queries.add(key)
            queries.append(query)
        if not queries:
            continue
        if name in seen_names:
            name = f"{name}-{len(out)+1}"
        seen_names.add(name)
        out.append(SearchFamily(
            name=name,
            queries=tuple(queries[:MAX_QUERIES_PER_FAMILY]),
            purpose=family.purpose,
            priority=max(0, min(int(family.priority), 100)),
            anchor=bool(family.anchor),
        ))
    return tuple(out)


def _select_initial_families(families: Iterable[SearchFamily], facets: Iterable[str]) -> tuple[SearchFamily, ...]:
    values = list(families)
    required = tuple(facets)

    def safety_rank(item: tuple[int, SearchFamily]) -> tuple[int, int, int, int]:
        index, family = item
        covers_required = any(_family_covers_facet(family, facet) for facet in required)
        anchor = family.anchor or _is_topic_family(family)
        return (-int(anchor), -int(covers_required), -int(family.priority), index)

    selected = [family for _, family in sorted(enumerate(values), key=safety_rank)[:MAX_INITIAL_FAMILIES]]
    selected.sort(key=lambda family: -int(family.priority))
    return tuple(selected)


def _parse_families(
    value: Any,
    *,
    max_families: int,
    total_limit: int,
    question: str,
) -> tuple[SearchFamily, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ModelOutputError("search planner query_families must be a list")
    out: list[SearchFamily] = []
    seen_queries: set[str] = set()
    total = 0
    for index, item in enumerate(value[:max_families]):
        if not isinstance(item, dict):
            continue
        name = _bounded_text(item.get("name"), default=f"family-{index + 1}", max_len=40)
        purpose = _bounded_purpose(item.get("purpose"), name=name)
        priority = _bounded_int(item.get("priority"), default=_default_family_priority(purpose), minimum=0, maximum=100)
        anchor = item.get("anchor", str(purpose) in {str(FamilyPurpose.TOPIC), str(FamilyPurpose.ENTITY), str(FamilyPurpose.INTERSECTION)})
        if not isinstance(anchor, bool):
            anchor = False
        raw_queries = item.get("queries")
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        if not isinstance(raw_queries, list):
            continue
        queries: list[str] = []
        for raw in raw_queries[:MAX_QUERIES_PER_FAMILY]:
            if not isinstance(raw, str):
                continue
            cleaned = sanitize_hint(raw, question=question, max_len=120)
            key = _near_duplicate_key(cleaned)
            if not key or key in seen_queries:
                continue
            seen_queries.add(key)
            queries.append(cleaned)
            total += 1
            if total >= total_limit:
                break
        if queries:
            out.append(SearchFamily(name=name, queries=tuple(queries), purpose=purpose, priority=priority, anchor=anchor))
        if total >= total_limit:
            break
    return tuple(out)


def _string_tuple(
    value: Any,
    *,
    max_items: int,
    max_len: int,
    question: str,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ModelOutputError("search planner list field has invalid type")
    out: list[str] = []
    seen: set[str] = set()
    for raw in value[:max_items]:
        if not isinstance(raw, str):
            continue
        text = sanitize_hint(raw, question=question, max_len=max_len)
        key = normalize_text(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def _required_aspects(searchable: bool, facets: Iterable[str]) -> tuple[str, ...]:
    if not searchable:
        return ()
    return _unique_text(("topic", *tuple(facets)))[:12]


def _deterministic_topic_anchors(question: str, facets: Iterable[str]) -> tuple[str, ...]:
    tokens = list(informative_tokens(question))
    facet_tokens: set[str] = set()
    for facet in facets:
        for value in facet_query_terms(facet):
            facet_tokens.update(informative_tokens(value))
    if "pediatric_population" in set(facets):
        facet_tokens.update(informative_tokens("بچه بچه ها کودک کودکان اطفال نوجوان child children pediatric adolescent"))
    anchors = [
        token for token in tokens
        if token not in facet_tokens and normalize_text(token) not in _GENERIC_TOPIC_QUALIFIERS
    ]
    if not anchors:
        anchors = tokens
    return tuple(anchors[:6])


def _deterministic_topic_anchor_groups(question: str, facets: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    """Build mandatory topic identity from user language, never model-invented hints.

    Resolved terminology forms one OR group per concept. Remaining deterministic
    topic tokens become singleton AND groups after facet/filler removal. This
    prevents a model-provided full-question anchor from making intent words such
    as timing or recommendation mandatory topic evidence.
    """
    try:
        resolved = DentalConceptResolver.load_default().resolve(question)
    except Exception:
        resolved = ()
    groups = list(concept_variant_groups(resolved))
    covered = {term for group in groups for term in group}
    anchors = _deterministic_topic_anchors(question, facets)
    for anchor in anchors:
        normalized = normalize_text(anchor)
        if normalized and not any(normalized == term or normalized in term or term in normalized for term in covered):
            groups.append((normalized,))
    return tuple(group for group in groups if group)[:6]


def _parse_topic_anchor_groups(value: Any, *, question: str) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ModelOutputError("search planner topic_anchor_groups must be a list")
    out: list[tuple[str, ...]] = []
    for raw_group in value[:6]:
        if isinstance(raw_group, str):
            raw_group = [raw_group]
        if not isinstance(raw_group, (list, tuple)):
            continue
        group = _string_tuple(raw_group, max_items=8, max_len=80, question=question)
        if group:
            out.append(group)
    return tuple(out)

def _deterministic_intersections(
    topic_anchors: Iterable[str],
    facets: Iterable[str],
    *,
    question: str,
) -> tuple[str, ...]:
    topics = tuple(topic_anchors)[:2]
    if not topics:
        return ()
    topic = " ".join(topics)
    out: list[str] = []
    for facet in facets:
        terms = facet_query_terms(facet)
        if not terms:
            continue
        value = sanitize_hint(f"{topic} {terms[0]}", question=question)
        if value:
            out.append(value)
        if len(out) >= 4:
            break
    return _unique_text(out)


def _mechanical_typo_hints(question: str) -> tuple[str, ...]:
    normalized = normalize_text(question)
    if not normalized:
        return ()
    collapsed = _REPEAT_RE.sub(r"\1", normalized)
    if collapsed != normalized and informative_query(collapsed):
        return (informative_query(collapsed),)
    return ()


def _family_covers_facet(family: SearchFamily, facet: str) -> bool:
    name = normalize_text(family.name).replace(" ", "_")
    if facet in name:
        return True
    if facet == "timing_age" and ("timing" in name or "age" in name or "سن" in name):
        return True
    if facet.endswith("population") and ("population" in name or "pedi" in name or "child" in name or "کود" in name):
        return True
    terms = {normalize_text(value) for value in facet_query_terms(facet)}
    queries = {normalize_text(query) for query in family.queries}
    return bool(terms & queries)


def _is_topic_family(family: SearchFamily) -> bool:
    return str(family.purpose) in {str(FamilyPurpose.TOPIC), str(FamilyPurpose.ENTITY)} or family.name.casefold() in {"topic", "entity"}


def _bounded_purpose(value: Any, *, name: str) -> str:
    allowed = {str(item) for item in FamilyPurpose}
    if isinstance(value, str) and value in allowed:
        return value
    normalized = name.casefold()
    if "topic" in normalized:
        return str(FamilyPurpose.TOPIC)
    if "entity" in normalized:
        return str(FamilyPurpose.ENTITY)
    if "population" in normalized or "pedi" in normalized:
        return str(FamilyPurpose.POPULATION)
    if "intersection" in normalized:
        return str(FamilyPurpose.INTERSECTION)
    if "termin" in normalized or "domain" in normalized:
        return str(FamilyPurpose.TERMINOLOGY)
    if "stage" in normalized:
        return str(FamilyPurpose.STAGE)
    if "alias" in normalized or "typo" in normalized:
        return str(FamilyPurpose.ALIAS)
    if "facet" in normalized or "tim" in normalized or "age" in normalized:
        return str(FamilyPurpose.FACET)
    return str(FamilyPurpose.OTHER)


def _default_family_priority(purpose: str) -> int:
    return {
        str(FamilyPurpose.TOPIC): 100,
        str(FamilyPurpose.ENTITY): 98,
        str(FamilyPurpose.POPULATION): 94,
        str(FamilyPurpose.FACET): 92,
        str(FamilyPurpose.INTERSECTION): 88,
        str(FamilyPurpose.STAGE): 80,
        str(FamilyPurpose.TERMINOLOGY): 76,
        str(FamilyPurpose.ALIAS): 70,
        str(FamilyPurpose.RESCUE): 68,
    }.get(str(purpose), 60)


def _intent_from_facets(facets: Iterable[str], *, searchable: bool) -> str:
    if not searchable:
        return "non_searchable"
    ordered = tuple(facets)
    for value in (
        "prevalence", "frequency", "epidemiology", "salary", "cost", "career", "regulation",
        "comparison", "recommendation", "differential_diagnosis", "diagnosis", "treatment",
        "contraindication", "recurrence", "follow_up", "cause_reason", "method_how", "timing_age",
        "dosage", "quantity", "indication", "complication", "prognosis", "timing",
    ):
        if value in ordered:
            return value
    return "archive_lookup"


def _bounded_pattern(value: Any, *, default: str) -> str:
    allowed = {str(item) for item in EvidencePattern}
    return value if isinstance(value, str) and value in allowed else default


def _near_duplicate_key(value: str) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    tokens = normalized.split()
    return " ".join(sorted(dict.fromkeys(tokens))) if len(tokens) > 1 else normalized


def _unique_text(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = normalize_text(text)
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)
    return tuple(out)


def _bounded_text(value: Any, *, default: str, max_len: int) -> str:
    if not isinstance(value, str):
        return default
    text = " ".join(value.strip().split())
    return text[:max_len] or default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


__all__ = [
    "PLANNER_VERSION", "QUERY_MODEL_VERSION", "SearchFamily", "SearchPlan",
    "RetrievalPolicy", "deterministic_fallback_plan", "infer_question_aspects",
    "observed_vocabulary", "parse_refinement_families", "parse_search_plan",
    "plan_from_payload", "plan_requires_deep_retrieval",
]
