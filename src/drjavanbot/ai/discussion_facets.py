from __future__ import annotations

from typing import Sequence
from drjavanbot.normalization import normalize_text, tokenize
from .planner import SearchPlan
from .discussion_types import _HitState

ASPECT_MARKERS: dict[str, tuple[str, ...]] = {
    "timing_age": ("timing", "age", "زمان", "سن", "سالگی", "when"),
    "pediatric_population": ("population", "pediatric", "child", "children", "کودک", "بچه", "اطفال", "نوجوان"),
    "comparison": ("comparison", "compare", "versus", "vs", "مقایسه"),
    "recommendation": ("recommendation", "recommend", "quality", "experience", "پیشنهاد", "توصیه", "تجربه"),
    "cause_reason": ("cause", "reason", "why", "علت", "دلیل"),
    "method_how": ("method", "how", "technique", "steps", "روش", "مراحل"),
    "quantity": ("quantity", "amount", "dose", "دوز", "مقدار"),
}

def _required_family_groups(plan: SearchPlan, anchor_families: set[str]) -> tuple[set[str], ...]:
    groups: list[set[str]] = []
    families = tuple(plan.query_families)
    for aspect in plan.required_aspects:
        normalized_aspect = normalize_text(aspect)
        if normalized_aspect in {"", "topic"}:
            continue
        markers = ASPECT_MARKERS.get(aspect, (aspect.replace("_", " "),))
        normalized_markers = tuple(normalize_text(marker) for marker in markers if normalize_text(marker))
        matched: set[str] = set()
        for family in families:
            haystacks = (
                normalize_text(family.name.replace("_", " ")),
                *(normalize_text(query) for query in family.queries),
            )
            if any(
                marker and marker in haystack
                for marker in normalized_markers
                for haystack in haystacks
            ):
                matched.add(family.name)
        if matched and matched not in groups:
            groups.append(matched)
    return tuple(groups)


def _anchor_family_names(plan: SearchPlan) -> set[str]:
    """Families that directly carry the user's core requested entity/topic."""
    core = tuple(
        normalize_text(value)
        for value in (*plan.core_concepts, *plan.aliases)
        if normalize_text(value) and len(normalize_text(value)) >= 2
    )
    out: set[str] = set()
    for family in plan.query_families:
        name = family.name.casefold()
        if any(token in name for token in ("topic", "core", "procedure", "product", "material", "entity")):
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
    concepts = tuple(
        normalize_text(value)
        for value in (*plan.core_concepts, *plan.aliases)
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
