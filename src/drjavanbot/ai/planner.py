from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_query, informative_tokens
from .validation import ModelOutputError, parse_json_object

PLANNER_VERSION = "semantic-search-plan-v2-faceted"
_MAX_INITIAL_FAMILIES = 6
_MAX_FINAL_FAMILIES = 10
_MAX_QUERIES_PER_FAMILY = 4
_MAX_TOTAL_QUERIES = 20


@dataclass(frozen=True, slots=True)
class SearchFamily:
    name: str
    queries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "queries": list(self.queries)}


@dataclass(frozen=True, slots=True)
class SearchPlan:
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

    @property
    def queries(self) -> tuple[tuple[str, str], ...]:
        """Return a bounded, family-balanced query schedule.

        The previous implementation exhausted early families before later ones.
        For faceted questions that could mean all topic queries ran while an age,
        population or mechanism family was silently dropped by the global cap.
        Round-robin scheduling guarantees every family gets a chance before a
        second/third spelling variant consumes capacity.
        """
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        families = tuple(self.query_families[:_MAX_FINAL_FAMILIES])
        for query_index in range(_MAX_QUERIES_PER_FAMILY):
            for family in families:
                if query_index >= len(family.queries):
                    continue
                value = family.queries[query_index]
                normalized = normalize_text(value)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                out.append((family.name, value))
                if len(out) >= _MAX_TOTAL_QUERIES:
                    return tuple(out)
        return tuple(out)

    def with_added_families(self, families: Iterable[SearchFamily]) -> "SearchPlan":
        """Add bounded refinement families without being blocked by a full initial plan."""
        merged: list[SearchFamily] = list(self.query_families[:_MAX_INITIAL_FAMILIES])
        names = {f.name.casefold() for f in merged}
        existing_queries = {normalize_text(q) for f in merged for q in f.queries if normalize_text(q)}
        for family in families:
            if len(merged) >= _MAX_FINAL_FAMILIES:
                break
            fresh = tuple(q for q in family.queries if normalize_text(q) not in existing_queries)
            if not fresh:
                continue
            name = family.name
            if name.casefold() in names:
                name = f"{name}-refined"
            merged.append(SearchFamily(name=name, queries=fresh[:_MAX_QUERIES_PER_FAMILY]))
            names.add(name.casefold())
            existing_queries.update(normalize_text(q) for q in fresh)
        return replace(self, query_families=tuple(merged))

    def summary(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "core_concepts": list(self.core_concepts[:8]),
            "required_aspects": list(self.required_aspects[:8]),
            "entity_types": list(self.entity_types[:4]),
            "query_families": [f.to_dict() for f in self.query_families[:_MAX_FINAL_FAMILIES]],
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
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, question: str = "") -> "SearchPlan":
        return _plan_from_payload(value, question=question)


def parse_search_plan(content: str, *, question: str) -> SearchPlan:
    return _plan_from_payload(parse_json_object(content), question=question)


def parse_refinement_families(content: str) -> tuple[SearchFamily, ...]:
    payload = parse_json_object(content)
    return _parse_families(payload.get("query_families"), max_families=4, total_limit=10)


def deterministic_fallback_plan(question: str) -> SearchPlan:
    """Fail-soft local plan. It expands no dental facts and still preserves question facets."""
    normalized = normalize_text(question)
    topical = informative_query(normalized)
    tokens = informative_tokens(normalized)
    searchable = bool(topical)
    families: list[SearchFamily] = []
    if topical:
        families.append(SearchFamily("topic", (topical,)))
    families.extend(_generic_aspect_families(question, existing=families))
    return SearchPlan(
        searchable=searchable,
        intent="archive_lookup" if searchable else "non_searchable",
        core_concepts=tokens[:8],
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=tuple(families[:_MAX_INITIAL_FAMILIES]),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=infer_question_aspects(question),
    )


def infer_question_aspects(question: str) -> tuple[str, ...]:
    """Infer generic answer facets without adding domain facts.

    This is a deterministic safety net for planner omissions. The terms describe
    *what kind of answer the user asks for* (timing, population, comparison...),
    not what the dental answer is.
    """
    normalized = normalize_text(question)
    checks = (
        ("timing_age", ("سن", "سنی", "سالگی", "چند سال", "چه زمانی", "زمان مناسب", "کی ", " age", "when", "timing")),
        ("pediatric_population", ("بچه", "بچه ها", "کودک", "کودکان", "اطفال", "نوجوان", "child", "children", "pediatric", "adolescent")),
        ("comparison", ("مقایسه", "کدام", "کدوم", "بهتر", " یا ", " vs ", "versus", "compare")),
        ("recommendation", ("پیشنهاد", "توصیه", "خوبه", "خوب است", "بهترین", "recommend", "best")),
        ("cause_reason", ("چرا", "علت", "دلیل", "cause", "reason", "why")),
        ("method_how", ("چطور", "چگونه", "روش", "مراحل", "how", "technique", "steps")),
        ("quantity", ("چقدر", "چند", "مقدار", "دوز", "dose", "how much", "how many")),
    )
    out: list[str] = []
    padded = f" {normalized} "
    for name, needles in checks:
        if any(normalize_text(needle).strip() in padded for needle in needles if normalize_text(needle).strip()):
            out.append(name)
    return tuple(out)


def plan_requires_deep_retrieval(plan: SearchPlan) -> bool:
    """Whether one superficially strong lexical pass is not enough to declare coverage."""
    demanding = {"timing_age", "comparison", "recommendation", "cause_reason", "method_how", "quantity"}
    aspects = set(plan.required_aspects)
    return bool(aspects & demanding) and (len(aspects) >= 1 or len(plan.query_families) >= 2)


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


def _plan_from_payload(payload: dict[str, Any], *, question: str) -> SearchPlan:
    searchable = payload.get("searchable", True)
    if not isinstance(searchable, bool):
        raise ModelOutputError("search planner searchable must be boolean")
    intent = _bounded_text(payload.get("intent"), default="archive_lookup", max_len=160)
    core = _string_tuple(payload.get("core_concepts"), max_items=10, max_len=80)
    aliases = _string_tuple(payload.get("aliases"), max_items=14, max_len=80)
    optional = _string_tuple(payload.get("optional_concepts"), max_items=10, max_len=80)
    entities = _string_tuple(payload.get("entity_types"), max_items=6, max_len=60)
    phrases = _string_tuple(payload.get("phrases"), max_items=8, max_len=100)
    excludes = _string_tuple(payload.get("exclude_terms"), max_items=8, max_len=60)
    low = _string_tuple(payload.get("low_information_terms"), max_items=10, max_len=40)
    model_aspects = _string_tuple(payload.get("required_aspects"), max_items=8, max_len=60)
    inferred_aspects = infer_question_aspects(question)
    aspects = _unique_text((*model_aspects, *inferred_aspects))[:8]
    reply_context = payload.get("reply_context", True)
    if not isinstance(reply_context, bool):
        reply_context = True
    families = list(_parse_families(payload.get("query_families"), max_families=_MAX_INITIAL_FAMILIES, total_limit=_MAX_TOTAL_QUERIES))
    families.extend(_generic_aspect_families(question, existing=families))
    families = list(_dedupe_families(families))[:_MAX_INITIAL_FAMILIES]

    if searchable and not families:
        fallback = deterministic_fallback_plan(question)
        families = list(fallback.query_families)
        if not core:
            core = fallback.core_concepts
    if searchable and not families:
        searchable = False
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
        reply_context=reply_context,
        required_aspects=aspects,
    )


def _generic_aspect_families(question: str, *, existing: Iterable[SearchFamily]) -> tuple[SearchFamily, ...]:
    aspects = set(infer_question_aspects(question))
    existing_names = {family.name.casefold() for family in existing}
    out: list[SearchFamily] = []
    if "timing_age" in aspects and not any("tim" in name or "age" in name or "سن" in name for name in existing_names):
        out.append(SearchFamily("facet_timing_age", ("سن", "سالگی", "زمان شروع", "age")))
    if "pediatric_population" in aspects and not any("child" in name or "pedi" in name or "کود" in name for name in existing_names):
        out.append(SearchFamily("facet_population", ("کودک", "بچه", "اطفال", "pediatric")))
    return tuple(out)


def _dedupe_families(families: Iterable[SearchFamily]) -> tuple[SearchFamily, ...]:
    out: list[SearchFamily] = []
    seen_names: set[str] = set()
    seen_queries: set[str] = set()
    for family in families:
        name = family.name.casefold().strip() or f"family-{len(out)+1}"
        queries: list[str] = []
        for query in family.queries:
            key = normalize_text(query)
            if not key or key in seen_queries:
                continue
            seen_queries.add(key)
            queries.append(query)
        if not queries:
            continue
        if name in seen_names:
            name = f"{name}-{len(out)+1}"
        seen_names.add(name)
        out.append(SearchFamily(name=name, queries=tuple(queries[:_MAX_QUERIES_PER_FAMILY])))
    return tuple(out)


def _parse_families(value: Any, *, max_families: int, total_limit: int) -> tuple[SearchFamily, ...]:
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
        raw_queries = item.get("queries")
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        if not isinstance(raw_queries, list):
            continue
        queries: list[str] = []
        for raw in raw_queries[:_MAX_QUERIES_PER_FAMILY]:
            if not isinstance(raw, str):
                continue
            cleaned = " ".join(raw.strip().split())[:120]
            normalized = normalize_text(cleaned)
            if not normalized or normalized in seen_queries:
                continue
            seen_queries.add(normalized)
            queries.append(cleaned)
            total += 1
            if total >= total_limit:
                break
        if queries:
            out.append(SearchFamily(name=name, queries=tuple(queries)))
        if total >= total_limit:
            break
    return tuple(out)


def _string_tuple(value: Any, *, max_items: int, max_len: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ModelOutputError("search planner list field has invalid type")
    out: list[str] = []
    seen: set[str] = set()
    for raw in value[:max_items]:
        if not isinstance(raw, str):
            continue
        text = " ".join(raw.strip().split())[:max_len]
        normalized = normalize_text(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(text)
    return tuple(out)


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


__all__ = [
    "PLANNER_VERSION", "SearchFamily", "SearchPlan", "deterministic_fallback_plan",
    "infer_question_aspects", "observed_vocabulary", "parse_refinement_families",
    "parse_search_plan", "plan_requires_deep_retrieval",
]
