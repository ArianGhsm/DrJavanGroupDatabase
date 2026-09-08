from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import re
from typing import Iterable

from drjavanbot.normalization import normalize_text, tokenize
from .models import Ambiguity, ConceptResolution


@dataclass(frozen=True, slots=True)
class _Concept:
    concept_id: str
    canonical: str
    terms: tuple[str, ...]
    entity_type: str
    subdomain: str | None


class DentalConceptResolver:
    """Terminology resolver only; it stores aliases, never clinical answer facts."""

    def __init__(self, concepts: Iterable[_Concept]) -> None:
        self._concepts = tuple(concepts)
        mapping: dict[str, list[_Concept]] = {}
        for concept in self._concepts:
            for raw in (concept.canonical, *concept.terms):
                term = normalize_text(raw)
                if term:
                    mapping.setdefault(term, []).append(concept)
        self._mapping = {term: tuple(values) for term, values in mapping.items()}
        self._terms = tuple(sorted(self._mapping, key=lambda value: (-len(tokenize(value)), -len(value), value)))

    @classmethod
    def load_default(cls) -> "DentalConceptResolver":
        concepts: list[_Concept] = []
        base = json.loads(files("drjavanbot").joinpath("data/dental_lexicon.json").read_text(encoding="utf-8"))
        for index, item in enumerate(base.get("groups", []), start=1):
            canonical = str(item.get("canonical", "")).strip()
            if not canonical:
                continue
            concepts.append(_Concept(
                concept_id=_slug(canonical) or f"legacy_{index}",
                canonical=canonical,
                terms=tuple(str(value) for value in item.get("terms", []) if str(value).strip()),
                entity_type="dental_term",
                subdomain=None,
            ))
        extra = json.loads(files("drjavanbot").joinpath("data/dental_concepts.json").read_text(encoding="utf-8"))
        override_ids = {str(item.get("id", "")) for item in extra.get("concepts", [])}
        concepts = [item for item in concepts if item.concept_id not in override_ids]
        for item in extra.get("concepts", []):
            concepts.append(_Concept(
                concept_id=str(item["id"]),
                canonical=str(item["canonical"]),
                terms=tuple(str(value) for value in item.get("terms", [])),
                entity_type=str(item.get("entity_type", "dental_term")),
                subdomain=(str(item["subdomain"]) if item.get("subdomain") else None),
            ))
        return cls(concepts)

    def resolve(self, question: str) -> tuple[ConceptResolution, ...]:
        normalized = normalize_text(question)
        if not normalized:
            return ()
        padded = f" {normalized} "
        matches: list[tuple[int, str, _Concept]] = []
        for term in self._terms:
            if not _term_present(padded, term):
                continue
            for concept in self._mapping[term]:
                if concept.concept_id == "cyst" and not self._cyst_is_noun(question, normalized):
                    continue
                matches.append((len(tokenize(term)), term, concept))
        # Prefer the most specific concept over its component concepts when both
        # are matched by the exact same span (e.g. odontogenic cyst vs cyst).
        selected: list[ConceptResolution] = []
        seen_ids: set[str] = set()
        compound_ids = {concept.concept_id for length, _term, concept in matches if length > 1}
        for _length, term, concept in sorted(matches, key=lambda item: (-item[0], -len(item[1]))):
            if concept.concept_id in seen_ids:
                continue
            if concept.concept_id in {"cyst", "odontogenic"} and "odontogenic_cyst" in compound_ids:
                # Keep descriptor resolution only when it independently contributes
                # useful terminology; the compound entity is the canonical topic.
                if concept.concept_id == "cyst":
                    continue
            variants = tuple(dict.fromkeys(normalize_text(value) for value in (concept.canonical, *concept.terms) if normalize_text(value)))
            selected.append(ConceptResolution(
                concept_id=concept.concept_id,
                canonical=concept.canonical,
                matched_text=term,
                variants=variants,
                entity_type=concept.entity_type,
                subdomain=concept.subdomain,
                confidence=0.98,
            ))
            seen_ids.add(concept.concept_id)
        return tuple(selected)

    def ambiguities(self, question: str) -> tuple[Ambiguity, ...]:
        normalized = normalize_text(question)
        if "کیست" not in tokenize(normalized):
            return ()
        noun = self._cyst_is_noun(question, normalized)
        return (Ambiguity(
            kind="homonym",
            span="کیست",
            candidates=("cyst", "who_is"),
            resolved_to="cyst" if noun else "who_is",
            confidence=0.96 if noun else 0.9,
        ),)

    @staticmethod
    def _cyst_is_noun(raw_question: str, normalized: str) -> bool:
        tokens = tokenize(normalized)
        positions = [index for index, token in enumerate(tokens) if token == "کیست"]
        if not positions:
            return False
        for index in positions:
            neighbors = set(tokens[max(0, index - 2): index] + tokens[index + 1:index + 4])
            if neighbors & {"ها", "های", "اودونتوژنیک", "اودنتوژنیک", "ادنتوژنیک", "odontogenic", "cyst", "cysts"}:
                return True
            if index == 0 and len(tokens) > 1 and tokens[1] in {"ها", "های"}:
                return True
        # Persian "X کیست؟" is the ordinary interrogative construction. Dental
        # noun use becomes clear through surrounding terminology/plural syntax.
        if re.search(r"\bکیست[\u200c\s]*(?:ها|های)\b", raw_question):
            return True
        return False


def concept_variant_groups(resolutions: Iterable[ConceptResolution]) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    for item in resolutions:
        values = tuple(dict.fromkeys((normalize_text(item.canonical), *item.variants)))
        cleaned = tuple(value for value in values if value)
        if cleaned:
            groups.append(cleaned)
    return tuple(groups)


def _term_present(padded: str, term: str) -> bool:
    if not term:
        return False
    if " " in term:
        return f" {term} " in padded or term in padded
    return re.search(rf"(?<![\w\u0600-\u06ff]){re.escape(term)}(?![\w\u0600-\u06ff])", padded) is not None


def _slug(value: str) -> str:
    normalized = normalize_text(value)
    ascii_only = re.sub(r"[^a-z0-9]+", "_", normalized)
    return ascii_only.strip("_")


__all__ = ["DentalConceptResolver", "concept_variant_groups"]
