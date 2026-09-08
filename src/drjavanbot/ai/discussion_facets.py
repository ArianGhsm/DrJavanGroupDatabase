from __future__ import annotations

from typing import Sequence
from drjavanbot.normalization import normalize_text, tokenize
from .planner import SearchPlan
from .discussion_types import _HitState

# Retrieval-language markers only; no dental answer facts are encoded here.
ASPECT_MARKERS: dict[str, tuple[str, ...]] = {
    "timing_age": ("timing", "age", "زمان", "سن", "سالگی", "when"),
    "timing": ("timing", "زمان", "زمان شروع", "when"),
    "pediatric_population": ("population", "pediatric", "child", "children", "کودک", "بچه", "اطفال", "نوجوان"),
    "population": ("population", "جمعیت", "گروه", "patient", "بیمار"),
    "condition": ("condition", "شرایط", "وضعیت"),
    "comparison": ("comparison", "compare", "versus", "vs", "مقایسه"),
    "recommendation": ("recommendation", "recommend", "quality", "experience", "پیشنهاد", "توصیه", "تجربه"),
    "cause_reason": ("cause", "reason", "why", "علت", "دلیل"),
    "method_how": ("method", "how", "technique", "steps", "روش", "مراحل"),
    "quantity": ("quantity", "amount", "مقدار", "تعداد"),
    "dosage": ("dosage", "dose", "دوز", "دوزاژ"),
    "indication": ("indication", "اندیکاسیون", "موارد استفاده"),
    "complication": ("complication", "عارضه", "عوارض"),
    "prognosis": ("prognosis", "پیش آگهی", "پروگنوز", "outcome"),
    "stage": ("stage", "phase", "مرحله", "فاز"),
}


def _aspect_key(value: object) -> str:
    # Keep schema underscores. `normalize_text()` intentionally normalizes
    # punctuation and would turn `timing_age` into a different lookup key.
    return str(value).strip().casefold().replace("-", "_").replace(" ", "_")


def _required_family_groups(plan: SearchPlan, anchor_families: set[str]) -> tuple[set[str], ...]:
    """Map typed requested facets to families without silently shrinking gaps."""
    groups: list[set[str]] = []
    families = tuple(plan.query_families)
    raw_aspects = (*tuple(getattr(plan, "answer_facets", ()) or ()), *tuple(plan.required_aspects or ()))
    aspects: list[str] = []
    seen_aspects: set[str] = set()
    for raw in raw_aspects:
        aspect = _aspect_key(raw)
        if not aspect or aspect == "topic" or aspect in seen_aspects:
            continue
        seen_aspects.add(aspect)
        aspects.append(aspect)

    for aspect in aspects:
        markers = ASPECT_MARKERS.get(aspect, (aspect.replace("_", " "),))
        normalized_markers = tuple(normalize_text(marker) for marker in markers if normalize_text(marker))
        matched: set[str] = set()
        for family in families:
            purpose = normalize_text(str(getattr(family, "purpose", "")).replace("_", " "))
            haystacks = (
                normalize_text(family.name.replace("_", " ")),
                purpose,
                *(normalize_text(query) for query in family.queries),
            )
            marker_match = any(
                marker and marker in haystack
                for marker in normalized_markers
                for haystack in haystacks
            )
            purpose_match = (
                (aspect in {"population", "pediatric_population"} and purpose == "population")
                or (aspect == "stage" and purpose == "stage")
            )
            if marker_match or purpose_match:
                matched.add(family.name)

        # Prefer a non-anchor facet family when one exists. An intersection can
        # still satisfy a facet only when there is no dedicated family.
        non_anchor = matched - anchor_families
        if non_anchor:
            matched = non_anchor

        group = matched or {f"__missing_required_facet__:{aspect}"}
        if group not in groups:
            groups.append(group)
    return tuple(groups)


def _anchor_family_names(plan: SearchPlan) -> set[str]:
    """Resolve topic anchors from typed metadata, plus bounded rescue anchors.

    Typed planner anchors are canonical. Refined/corpus families are unioned
    because a weak first pass can discover the actual archive vocabulary only in
    the bounded second pass. Generic `rescue` families do not automatically
    become anchors.
    """
    typed = {
        family.name
        for family in tuple(getattr(plan, "anchor_families", ()) or ())
        if getattr(family, "name", "")
    }
    typed.update(
        family.name
        for family in plan.query_families
        if _is_topic_refinement_family(family.name.casefold())
    )
    if typed:
        return typed

    core_values = tuple(getattr(plan, "topic_anchors", ()) or ()) or tuple(plan.core_concepts)
    core = tuple(
        normalize_text(value)
        for value in (*core_values, *plan.aliases)
        if normalize_text(value) and len(normalize_text(value)) >= 2
    )
    out: set[str] = set()
    for family in plan.query_families:
        if bool(getattr(family, "anchor", False)):
            out.add(family.name)
            continue
        purpose = normalize_text(str(getattr(family, "purpose", "")))
        if purpose in {"topic", "entity", "intersection"}:
            out.add(family.name)
            continue
        name = family.name.casefold()
        if any(token in name for token in ("topic", "core", "procedure", "product", "material", "entity")):
            out.add(family.name)
            continue
        if _is_topic_refinement_family(name):
            out.add(family.name)
            continue
        for query in family.queries:
            normalized = normalize_text(query)
            if any(_query_equivalent_to_concept(term, normalized) for term in core):
                out.add(family.name)
                break
    if not out and plan.query_families:
        out.add(plan.query_families[0].name)
    return out


def _is_topic_refinement_family(name: str) -> bool:
    normalized = normalize_text(name.replace("_", " "))
    tokens = set(tokenize(normalized))
    return bool(tokens & {"refined", "refinement", "corpus"})


def _query_equivalent_to_concept(concept: str, query: str) -> bool:
    if not concept or not query:
        return False
    if concept == query:
        return True
    concept_compact = "".join(tokenize(concept))
    query_compact = "".join(tokenize(query))
    return bool(concept_compact and len(concept_compact) >= 3 and concept_compact == query_compact)


def _mark_topic_anchors(
    states: Sequence[_HitState],
    *,
    plan: SearchPlan,
    anchor_families: set[str],
) -> None:
    topic_values = tuple(getattr(plan, "topic_anchors", ()) or ()) or tuple(plan.core_concepts)
    concepts = tuple(
        normalize_text(value)
        for value in (*topic_values, *plan.aliases)
        if normalize_text(value)
    )
    for state in states:
        direct = normalize_text(state.candidate.message.text_normalized or state.candidate.message.text_raw)
        canonical_qualified = bool(state.qualified_families & anchor_families)
        direct_core = any(_concept_in_text(concept, direct) for concept in concepts)
        state.topic_anchor = canonical_qualified or direct_core


def _concept_in_text(concept: str, text: str) -> bool:
    if not concept or not text:
        return False
    if concept in text:
        return True
    concept_compact = "".join(tokenize(concept))
    if len(concept_compact) < 3:
        return False
    text_compact = "".join(tokenize(text))
    return concept_compact in text_compact
