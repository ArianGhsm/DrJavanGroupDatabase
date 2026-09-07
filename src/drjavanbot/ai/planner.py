from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_query, informative_tokens
from .validation import ModelOutputError, parse_json_object

PLANNER_VERSION = "semantic-search-plan-v1"
_MAX_INITIAL_FAMILIES = 5
_MAX_FINAL_FAMILIES = 8
_MAX_QUERIES_PER_FAMILY = 4
_MAX_TOTAL_QUERIES = 14


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

    @property
    def queries(self) -> tuple[tuple[str, str], ...]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for family in self.query_families:
            for value in family.queries:
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
            "core_concepts": list(self.core_concepts[:6]),
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
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, question: str = "") -> "SearchPlan":
        return _plan_from_payload(value, question=question)


def parse_search_plan(content: str, *, question: str) -> SearchPlan:
    return _plan_from_payload(parse_json_object(content), question=question)


def parse_refinement_families(content: str) -> tuple[SearchFamily, ...]:
    payload = parse_json_object(content)
    return _parse_families(payload.get("query_families"), max_families=3, total_limit=8)


def deterministic_fallback_plan(question: str) -> SearchPlan:
    """Fail-soft local plan. It expands no facts and therefore cannot hallucinate evidence."""
    normalized = normalize_text(question)
    topical = informative_query(normalized)
    tokens = informative_tokens(normalized)
    searchable = bool(topical)
    families = (SearchFamily("topic", (topical,)),) if topical else ()
    return SearchPlan(
        searchable=searchable,
        intent="archive_lookup" if searchable else "non_searchable",
        core_concepts=tokens[:6],
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=families,
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
    )


def observed_vocabulary(candidates: Iterable[Any], *, question: str, limit: int = 28) -> tuple[str, ...]:
    from drjavanbot.search.terms import distinctive_terms

    texts: list[str | None] = []
    for candidate in list(candidates)[:12]:
        message = getattr(candidate, "message", None)
        texts.append(getattr(message, "text_normalized", None) or getattr(message, "text_raw", None))
        for context in getattr(candidate, "context", ())[:4]:
            texts.append(getattr(context, "text_normalized", None) or getattr(context, "text_raw", None))
        texts.extend(getattr(candidate, "matched_terms", ()))
    return distinctive_terms(texts, exclude=(question,), limit=limit)


def _plan_from_payload(payload: dict[str, Any], *, question: str) -> SearchPlan:
    searchable = payload.get("searchable", True)
    if not isinstance(searchable, bool):
        raise ModelOutputError("search planner searchable must be boolean")
    intent = _bounded_text(payload.get("intent"), default="archive_lookup", max_len=160)
    core = _string_tuple(payload.get("core_concepts"), max_items=8, max_len=80)
    aliases = _string_tuple(payload.get("aliases"), max_items=10, max_len=80)
    optional = _string_tuple(payload.get("optional_concepts"), max_items=8, max_len=80)
    entities = _string_tuple(payload.get("entity_types"), max_items=6, max_len=60)
    phrases = _string_tuple(payload.get("phrases"), max_items=6, max_len=100)
    excludes = _string_tuple(payload.get("exclude_terms"), max_items=8, max_len=60)
    low = _string_tuple(payload.get("low_information_terms"), max_items=10, max_len=40)
    reply_context = payload.get("reply_context", True)
    if not isinstance(reply_context, bool):
        reply_context = True
    families = _parse_families(payload.get("query_families"), max_families=_MAX_INITIAL_FAMILIES, total_limit=_MAX_TOTAL_QUERIES)

    if searchable and not families:
        fallback = deterministic_fallback_plan(question)
        families = fallback.query_families
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
        query_families=families,
        phrases=phrases,
        exclude_terms=excludes,
        low_information_terms=low,
        reply_context=reply_context,
    )


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


def _bounded_text(value: Any, *, default: str, max_len: int) -> str:
    if not isinstance(value, str):
        return default
    text = " ".join(value.strip().split())
    return text[:max_len] or default


__all__ = [
    "PLANNER_VERSION", "SearchFamily", "SearchPlan", "deterministic_fallback_plan",
    "observed_vocabulary", "parse_refinement_families", "parse_search_plan",
]
