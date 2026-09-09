from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import EntityMention, Geography, QuestionUnderstanding
from .understanding import QuestionContext

CONVERSATION_CONTEXT_VERSION = "conversation-question-context-v2.0"


@dataclass(frozen=True, slots=True)
class ConversationQuestionContext:
    domain: str | None = None
    subdomain: str | None = None
    entities: tuple[EntityMention, ...] = ()
    geography: Geography = Geography()
    version: str = CONVERSATION_CONTEXT_VERSION

    @classmethod
    def from_understanding(cls, understanding: QuestionUnderstanding) -> "ConversationQuestionContext":
        # Persist semantic interpretation only; raw question/evidence never enters conversation context storage.
        entities = tuple(
            EntityMention(
                text=item.canonical_label, canonical_id=item.canonical_id, canonical_label=item.canonical_label,
                entity_type=item.entity_type, variants=item.variants[:8], inferred=True,
                confidence=min(float(item.confidence), 0.82), subdomain=item.subdomain,
            )
            for item in understanding.entities[:6] if item.canonical_id and item.canonical_label
        )
        return cls(domain=understanding.domain, subdomain=understanding.subdomain, entities=entities, geography=understanding.geography)

    def to_question_context(self) -> QuestionContext:
        return QuestionContext(
            default_domain="dentistry", default_profession="dentistry", default_country_code="IR", default_country_label="Iran",
            recent_entities=self.entities, recent_subdomain=self.subdomain, recent_geography=self.geography,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "domain": self.domain, "subdomain": self.subdomain,
            "entities": [{"canonical_id": e.canonical_id, "canonical_label": e.canonical_label, "entity_type": e.entity_type,
                          "variants": list(e.variants), "confidence": e.confidence, "subdomain": e.subdomain} for e in self.entities],
            "geography": {"country_code": self.geography.country_code, "label": self.geography.label,
                          "explicit": self.geography.explicit, "source": self.geography.source},
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConversationQuestionContext":
        if value.get("version") != CONVERSATION_CONTEXT_VERSION:
            return cls()
        entities: list[EntityMention] = []
        for raw in value.get("entities", [])[:6]:
            if not isinstance(raw, dict): continue
            label = str(raw.get("canonical_label") or "").strip(); cid = str(raw.get("canonical_id") or "").strip()
            if not label or not cid: continue
            entities.append(EntityMention(text=label, canonical_id=cid, canonical_label=label,
                entity_type=str(raw.get("entity_type") or "dental_term"), variants=tuple(str(x) for x in raw.get("variants", [])[:8]),
                inferred=True, confidence=float(raw.get("confidence") or 0.7), subdomain=(str(raw.get("subdomain")) if raw.get("subdomain") else None)))
        geo = value.get("geography") if isinstance(value.get("geography"), dict) else {}
        geography = Geography(country_code=geo.get("country_code"), label=geo.get("label"), explicit=False, source="conversation")
        return cls(domain=value.get("domain"), subdomain=value.get("subdomain"), entities=tuple(entities), geography=geography)


__all__ = ["ConversationQuestionContext", "CONVERSATION_CONTEXT_VERSION"]
