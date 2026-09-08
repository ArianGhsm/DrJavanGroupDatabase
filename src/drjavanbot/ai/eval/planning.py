from __future__ import annotations

from dataclasses import replace
import re

from drjavanbot.ai.planner import deterministic_fallback_plan
from drjavanbot.ai.query_model import (
    EvidencePattern,
    FamilyPurpose,
    MAX_FINAL_FAMILIES,
    MAX_QUERIES_PER_FAMILY,
    MAX_TOTAL_QUERIES,
    RetrievalDepth,
    RetrievalPolicy,
    SearchFamily,
    SearchPlan,
)
from drjavanbot.ai.search_policy import derive_retrieval_policy, infer_question_facets
from drjavanbot.normalization import normalize_text, tokenize
from .schema import GoldenCase


# Evaluator metadata predates the typed query-understanding contract.  This
# adapter translates only generic intent/facet labels; it contains no dental
# answer facts, ages, doses, products or archive-specific message identifiers.
_FAMILY_FACET_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("experience", "recommend"), "recommendation"),
    (("comparison", "compare"), "comparison"),
    (("method", "technique"), "method_how"),
    (("cause", "reason"), "cause_reason"),
    (("quantity", "amount"), "quantity"),
    (("dose", "dosage"), "dosage"),
)
_AGE_MARKERS = frozenset({"سن", "سال", "سالگی", "ماه", "age", "year", "month"})
_PEDIATRIC_MARKERS = frozenset({"کودک", "کودکان", "بچه", "بچه ها", "اطفال", "نوجوان", "child", "children", "pediatric", "adolescent"})
_ACRONYM_RE = re.compile(r"^[A-Za-z](?:[.\-_ ]?[A-Za-z]){1,5}$")


def quality_search_plan(case: GoldenCase) -> SearchPlan:
    """Adapt frozen Quality Lab query metadata to ``SearchPlan`` v1.

    The frozen suite owns its query strings and gold identities.  The adapter
    preserves those inputs while supplying typed family purpose, canonical
    facets and a budget large enough to execute every frozen family variant.
    This keeps BASE-vs-candidate evaluation comparable after planner-v2 changed
    the default scheduler/policy semantics.
    """
    if not case.query_families:
        fallback = deterministic_fallback_plan(case.question)
        return replace(
            fallback,
            searchable=False,
            intent=case.category,
            normalized_intent=case.category,
            core_concepts=(),
            topic_anchors=(),
            aliases=(),
            optional_concepts=(),
            query_families=(),
            required_aspects=(),
            answer_facets=(),
            population_constraints=(),
            reply_context=False,
            expected_evidence_pattern=str(EvidencePattern.SINGLE_MESSAGE),
            retrieval_policy=RetrievalPolicy(
                depth=RetrievalDepth.DIRECT,
                query_budget=1,
                family_budget=1,
                per_family_budget=1,
                rescue_allowed=False,
                max_rescue_families=0,
                expected_evidence_pattern=EvidencePattern.SINGLE_MESSAGE,
                stop_when_required_facets_covered=True,
                minimum_family_coverage=1,
            ).bounded(),
        )

    inferred = tuple(infer_question_facets(case.question))
    facets = _typed_facets(case, inferred)
    families = _typed_families(case, facets)
    topic_anchors = case.topic_anchors or deterministic_fallback_plan(case.question).topic_anchors

    base_policy = derive_retrieval_policy(case.question, facets=facets, family_count=len(families))
    frozen_unique_queries = _unique_query_count(case)
    max_family_queries = max((len(family.queries) for family in families), default=1)
    depth = base_policy.depth
    # BASE Quality Lab predates planner-v2's narrow direct policy.  Keep direct
    # evaluator cases at standard retrieval breadth so the candidate engine is
    # compared against the same frozen top-K contract rather than a new cost cap.
    if str(depth) == str(RetrievalDepth.DIRECT):
        depth = RetrievalDepth.STANDARD
    policy = RetrievalPolicy(
        depth=depth,
        query_budget=min(MAX_TOTAL_QUERIES, max(base_policy.query_budget, frozen_unique_queries)),
        family_budget=min(MAX_FINAL_FAMILIES, max(base_policy.family_budget, len(families))),
        per_family_budget=min(MAX_QUERIES_PER_FAMILY, max(base_policy.per_family_budget, max_family_queries)),
        rescue_allowed=base_policy.rescue_allowed,
        max_rescue_families=base_policy.max_rescue_families,
        expected_evidence_pattern=base_policy.expected_evidence_pattern,
        stop_when_required_facets_covered=base_policy.stop_when_required_facets_covered,
        minimum_family_coverage=base_policy.minimum_family_coverage,
    ).bounded()

    required_aspects = tuple(dict.fromkeys(("topic", *facets)))
    optional = tuple(group[0] for group in case.required_facets if group)
    return SearchPlan(
        searchable=True,
        intent=case.category,
        core_concepts=topic_anchors or (case.question,),
        aliases=tuple(value for value in case.topic_anchors[1:] if value),
        optional_concepts=optional,
        entity_types=(),
        query_families=families,
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=case.reply_context or str(policy.expected_evidence_pattern) != str(EvidencePattern.SINGLE_MESSAGE),
        required_aspects=required_aspects,
        normalized_intent=case.category,
        topic_anchors=topic_anchors,
        answer_facets=facets,
        population_constraints=("pediatric_population",) if "pediatric_population" in facets else (),
        expected_evidence_pattern=str(policy.expected_evidence_pattern),
        retrieval_policy=policy,
    )


def _typed_families(case: GoldenCase, facets: tuple[str, ...]) -> tuple[SearchFamily, ...]:
    out: list[SearchFamily] = []
    for index, (name, queries) in enumerate(case.query_families):
        cleaned = tuple(query for query in queries if normalize_text(query))[:MAX_QUERIES_PER_FAMILY]
        if not cleaned:
            continue
        purpose, anchor = _family_role(case, name, cleaned, facets=facets, first=(index == 0))

        # Short acronyms are ambiguous in natural language.  When the frozen
        # evaluator also supplies expanded aliases, keep the acronym and the
        # expanded terminology in separate anchor families.  This preserves all
        # frozen queries while allowing discussion fusion to reward corroborated
        # senses instead of treating every acronym occurrence as equivalent.
        acronym, expanded = _partition_acronym_aliases(cleaned)
        if anchor and acronym and expanded:
            out.append(SearchFamily(
                name=name,
                queries=acronym,
                purpose=purpose,
                priority=100 if purpose == FamilyPurpose.TOPIC else 98,
                anchor=True,
            ))
            out.append(SearchFamily(
                name=f"{name}_aliases",
                queries=expanded,
                purpose=FamilyPurpose.ALIAS,
                priority=96,
                anchor=True,
            ))
            continue

        priority = _family_priority(purpose, anchor=anchor)
        out.append(SearchFamily(name=name, queries=cleaned, purpose=purpose, priority=priority, anchor=anchor))
    return tuple(out[:MAX_FINAL_FAMILIES])


def _family_role(
    case: GoldenCase,
    name: str,
    queries: tuple[str, ...],
    *,
    facets: tuple[str, ...],
    first: bool,
) -> tuple[str, bool]:
    normalized_name = normalize_text(name).replace(" ", "_")
    if "population" in normalized_name or "pedi" in normalized_name:
        return str(FamilyPurpose.POPULATION), False
    if "intersection" in normalized_name:
        return str(FamilyPurpose.INTERSECTION), True
    if "alias" in normalized_name or "typo" in normalized_name:
        return str(FamilyPurpose.ALIAS), False
    if "termin" in normalized_name:
        return str(FamilyPurpose.TERMINOLOGY), False
    if "stage" in normalized_name:
        return str(FamilyPurpose.STAGE), False
    if normalized_name in {"experience", "recommend", "comparison", "compare", "method", "technique", "cause", "reason", "quantity", "amount", "dose", "dosage", "timing", "age", "symptom", "disagreement", "tooth", "facet"}:
        return str(FamilyPurpose.FACET), False
    if any(token in normalized_name for token in ("topic", "product", "entity", "procedure", "material")):
        purpose = FamilyPurpose.ENTITY if any(token in normalized_name for token in ("product", "entity", "material")) else FamilyPurpose.TOPIC
        return str(purpose), True
    if _family_overlaps_topic(case, queries):
        return str(FamilyPurpose.TOPIC), True
    if first:
        return str(FamilyPurpose.TOPIC), True
    return str(FamilyPurpose.OTHER), False


def _typed_facets(case: GoldenCase, inferred: tuple[str, ...]) -> tuple[str, ...]:
    facets: list[str] = list(inferred)
    normalized_groups = tuple(
        frozenset(normalize_text(value) for value in group if normalize_text(value))
        for group in case.required_facets
    )
    group_tokens = set().union(*normalized_groups) if normalized_groups else set()

    for name, _queries in case.query_families:
        normalized_name = normalize_text(name).replace(" ", "_")
        if "population" in normalized_name:
            facet = "pediatric_population" if _has_marker(group_tokens, _PEDIATRIC_MARKERS) else "population"
            _append_unique(facets, facet)
        elif "timing" in normalized_name or normalized_name == "age":
            facet = "timing_age" if ("timing_age" in facets or _has_marker(group_tokens, _AGE_MARKERS)) else "timing"
            _append_unique(facets, facet)
        else:
            for hints, facet in _FAMILY_FACET_HINTS:
                if any(hint in normalized_name for hint in hints):
                    if facet == "quantity" and "dosage" in facets:
                        facet = "dosage"
                    _append_unique(facets, facet)
                    break

    # A frozen facet group can carry age/population semantics even if the family
    # label is generic.  Translate only generic language markers.
    if _has_marker(group_tokens, _PEDIATRIC_MARKERS):
        _append_unique(facets, "pediatric_population")
    if _has_marker(group_tokens, _AGE_MARKERS) and any("tim" in normalize_text(name) or "age" in normalize_text(name) for name, _ in case.query_families):
        _append_unique(facets, "timing_age")
        if "timing" in facets:
            facets.remove("timing")
    return tuple(facets[:12])


def _family_overlaps_topic(case: GoldenCase, queries: tuple[str, ...]) -> bool:
    anchors = tuple(normalize_text(value) for value in case.topic_anchors if normalize_text(value))
    if not anchors:
        return False
    for query in queries:
        normalized = normalize_text(query)
        if not normalized:
            continue
        for anchor in anchors:
            if _equivalent_or_contains(anchor, normalized):
                return True
    return False


def _equivalent_or_contains(left: str, right: str) -> bool:
    if left == right:
        return True
    left_tokens = tuple(tokenize(left))
    right_tokens = tuple(tokenize(right))
    if len(left_tokens) == 1 and len(left_tokens[0]) < 3:
        return False
    if len(right_tokens) == 1 and len(right_tokens[0]) < 3:
        return False
    return left in right or right in left


def _partition_acronym_aliases(queries: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    acronym: list[str] = []
    expanded: list[str] = []
    for query in queries:
        raw = " ".join(query.strip().split())
        compact = re.sub(r"[.\-_ ]", "", raw)
        is_acronym = bool(
            _ACRONYM_RE.fullmatch(raw)
            and 2 <= len(compact) <= 6
            and compact.isascii()
            and compact.isalpha()
            and compact.upper() == compact
        )
        (acronym if is_acronym else expanded).append(query)
    return tuple(acronym), tuple(expanded)


def _unique_query_count(case: GoldenCase) -> int:
    seen: set[str] = set()
    for _name, queries in case.query_families:
        for query in queries:
            normalized = normalize_text(query)
            if normalized:
                seen.add(normalized)
    return max(1, len(seen))


def _family_priority(purpose: str, *, anchor: bool) -> int:
    if anchor:
        return 100
    return {
        str(FamilyPurpose.POPULATION): 94,
        str(FamilyPurpose.FACET): 92,
        str(FamilyPurpose.INTERSECTION): 88,
        str(FamilyPurpose.STAGE): 80,
        str(FamilyPurpose.TERMINOLOGY): 76,
        str(FamilyPurpose.ALIAS): 70,
    }.get(str(purpose), 60)


def _has_marker(values: set[str], markers: frozenset[str]) -> bool:
    normalized_markers = tuple(normalize_text(value) for value in markers if normalize_text(value))
    return any(
        marker == value or marker in value or value in marker
        for value in values
        for marker in normalized_markers
    )


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


__all__ = ["quality_search_plan"]
