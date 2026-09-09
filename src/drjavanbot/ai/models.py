from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class UsageMetrics:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_irt: float | None = None
    cost_unit: str | None = None
    exchange_rate: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    model: str
    usage: UsageMetrics
    latency_ms: float
    request_id: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceMessage:
    source_ref: str
    message_id: int | None
    author: str | None
    datetime: str | None
    source_file: str
    text: str
    role: str
    local_score: float | None = None
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    match_reasons: tuple[str, ...] = field(default_factory=tuple)
    parent_source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "message_id": self.message_id,
            "author": self.author,
            "datetime": self.datetime,
            "source_file": self.source_file,
            "text": self.text,
            "role": self.role,
            "local_score": self.local_score,
            "matched_terms": list(self.matched_terms),
            "match_reasons": list(self.match_reasons),
            "parent_source_ref": self.parent_source_ref,
        }


@dataclass(frozen=True, slots=True)
class EvidencePack:
    question: str
    normalized_question: str
    budget_name: str
    estimated_tokens: int
    messages: tuple[EvidenceMessage, ...]

    @property
    def source_refs(self) -> frozenset[str]:
        return frozenset(item.source_ref for item in self.messages)

    @property
    def message_ids(self) -> frozenset[int]:
        return frozenset(item.message_id for item in self.messages if item.message_id is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "normalized_question": self.normalized_question,
            "budget_name": self.budget_name,
            "estimated_tokens": self.estimated_tokens,
            "evidence": [item.to_dict() for item in self.messages],
        }


@dataclass(frozen=True, slots=True)
class ClaimSupport:
    """A locally verified excerpt from one supplied archive message."""

    message_id: int
    source_ref: str
    quote: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "source_ref": self.source_ref,
            "quote": self.quote,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ClaimSupport":
        return cls(
            message_id=int(value["message_id"]),
            source_ref=str(value.get("source_ref", "")),
            quote=str(value.get("quote", "")),
        )


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    """One displayable statement whose supports were verified against EVIDENCE."""

    kind: str
    text: str
    supports: tuple[ClaimSupport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "supports": [item.to_dict() for item in self.supports],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GroundedClaim":
        supports = value.get("supports", [])
        return cls(
            kind=str(value.get("kind", "finding")),
            text=str(value.get("text", "")),
            supports=tuple(
                ClaimSupport.from_dict(item)
                for item in supports
                if isinstance(item, dict) and "message_id" in item
            ),
        )


@dataclass(frozen=True, slots=True)
class SourceSupport:
    evidence_id: str
    source_type: str
    source_ref: str
    quote: str
    support_class: str = "exact"

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "source_type": self.source_type, "source_ref": self.source_ref, "quote": self.quote, "support_class": self.support_class}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceSupport":
        return cls(evidence_id=str(value.get("evidence_id", "")), source_type=str(value.get("source_type", "")), source_ref=str(value.get("source_ref", "")), quote=str(value.get("quote", "")), support_class=str(value.get("support_class", "exact")))


@dataclass(frozen=True, slots=True)
class GroundedSourceClaim:
    kind: str
    text: str
    supports: tuple[SourceSupport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "supports": [item.to_dict() for item in self.supports]}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GroundedSourceClaim":
        raw = value.get("supports", [])
        return cls(kind=str(value.get("kind", "finding")), text=str(value.get("text", "")), supports=tuple(SourceSupport.from_dict(item) for item in raw if isinstance(item, dict)))


@dataclass(frozen=True, slots=True)
class AnswerResult:
    direct_answer: str
    key_findings: tuple[str, ...]
    disagreements: tuple[str, ...]
    practical_conclusion: str | None
    confidence: str
    confidence_reason: str
    cited_message_ids: tuple[int, ...]
    source_refs: tuple[str, ...]
    evidence_used_count: int
    independent_authors_count: int
    insufficient_evidence: bool
    safety_note_if_needed: str | None
    grounded_claims: tuple[GroundedClaim, ...] = field(default_factory=tuple)
    source_mode: str = "archive"
    external_sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    grounded_source_claims: tuple[GroundedSourceClaim, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    ai_calls: int = 0
    expansion_used: bool = False
    evidence_pack_estimated_tokens: int = 0

    def with_runtime(self, *, cache_hit: bool | None = None, ai_calls: int | None = None, expansion_used: bool | None = None) -> "AnswerResult":
        return replace(
            self,
            cache_hit=self.cache_hit if cache_hit is None else cache_hit,
            ai_calls=self.ai_calls if ai_calls is None else ai_calls,
            expansion_used=self.expansion_used if expansion_used is None else expansion_used,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "direct_answer": self.direct_answer,
            "key_findings": list(self.key_findings),
            "disagreements": list(self.disagreements),
            "practical_conclusion": self.practical_conclusion,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "cited_message_ids": list(self.cited_message_ids),
            "source_refs": list(self.source_refs),
            "evidence_used_count": self.evidence_used_count,
            "independent_authors_count": self.independent_authors_count,
            "insufficient_evidence": self.insufficient_evidence,
            "safety_note_if_needed": self.safety_note_if_needed,
            "grounded_claims": [item.to_dict() for item in self.grounded_claims],
            "source_mode": self.source_mode,
            "external_sources": [dict(item) for item in self.external_sources],
            "grounded_source_claims": [item.to_dict() for item in self.grounded_source_claims],
            "limitations": list(self.limitations),
            "cache_hit": self.cache_hit,
            "ai_calls": self.ai_calls,
            "expansion_used": self.expansion_used,
            "evidence_pack_estimated_tokens": self.evidence_pack_estimated_tokens,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AnswerResult":
        claims = value.get("grounded_claims", [])
        source_claims = value.get("grounded_source_claims", [])
        return cls(
            direct_answer=str(value.get("direct_answer", "")),
            key_findings=tuple(str(x) for x in value.get("key_findings", [])),
            disagreements=tuple(str(x) for x in value.get("disagreements", [])),
            practical_conclusion=_optional_str(value.get("practical_conclusion")),
            confidence=str(value.get("confidence", "low")),
            confidence_reason=str(value.get("confidence_reason", "")),
            cited_message_ids=tuple(int(x) for x in value.get("cited_message_ids", [])),
            source_refs=tuple(str(x) for x in value.get("source_refs", [])),
            evidence_used_count=int(value.get("evidence_used_count", 0)),
            independent_authors_count=int(value.get("independent_authors_count", 0)),
            insufficient_evidence=bool(value.get("insufficient_evidence", False)),
            safety_note_if_needed=_optional_str(value.get("safety_note_if_needed")),
            grounded_claims=tuple(
                GroundedClaim.from_dict(item) for item in claims if isinstance(item, dict)
            ),
            source_mode=str(value.get("source_mode", "archive")),
            external_sources=tuple(dict(item) for item in value.get("external_sources", []) if isinstance(item, dict)),
            grounded_source_claims=tuple(GroundedSourceClaim.from_dict(item) for item in source_claims if isinstance(item, dict)),
            limitations=tuple(str(item) for item in value.get("limitations", [])),
            cache_hit=bool(value.get("cache_hit", False)),
            ai_calls=int(value.get("ai_calls", 0)),
            expansion_used=bool(value.get("expansion_used", False)),
            evidence_pack_estimated_tokens=int(value.get("evidence_pack_estimated_tokens", 0)),
        )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
