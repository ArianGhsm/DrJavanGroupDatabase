from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from drjavanbot.normalization import normalize_text


class DentalLexicon:
    def __init__(self, groups: tuple[tuple[str, ...], ...], *, version: int = 1) -> None:
        self.groups = groups
        self.version = version
        mapping: dict[str, set[str]] = {}
        for group in groups:
            normalized = {normalize_text(term) for term in group if normalize_text(term)}
            for term in normalized:
                mapping.setdefault(term, set()).update(normalized - {term})
        self._mapping = {key: tuple(sorted(values)) for key, values in mapping.items()}

    @classmethod
    def load_default(cls) -> "DentalLexicon":
        path = files("drjavanbot").joinpath("data/dental_lexicon.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        groups = tuple(
            tuple([item["canonical"], *item.get("terms", [])])
            for item in payload.get("groups", [])
        )
        return cls(groups, version=int(payload.get("version", 1)))

    @classmethod
    def from_path(cls, path: Path) -> "DentalLexicon":
        payload = json.loads(path.read_text(encoding="utf-8"))
        groups = tuple(tuple([item["canonical"], *item.get("terms", [])]) for item in payload["groups"])
        return cls(groups, version=int(payload.get("version", 1)))

    def expand(self, query: str, *, limit: int = 24) -> tuple[str, ...]:
        normalized = normalize_text(query)
        if not normalized:
            return ()
        padded = f" {normalized} "
        expansions: list[str] = []
        seen = {normalized}
        for term, related in self._mapping.items():
            if f" {term} " not in padded and normalized != term:
                continue
            for candidate in related:
                if candidate not in seen:
                    seen.add(candidate)
                    expansions.append(candidate)
                    if len(expansions) >= limit:
                        return tuple(expansions)
        return tuple(expansions)
