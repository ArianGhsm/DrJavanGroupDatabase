from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from drjavanbot.ai.models import GroundedSourceClaim, SourceSupport
from drjavanbot.normalization import normalize_text
from .fusion import RankedEvidence
from .models import EvidenceItem


class MultiSourceGroundingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SupportSpan:
    """A locally-created, verbatim support span exposed to synthesis by opaque ID."""

    span_id: str
    evidence_id: str
    quote: str


def parse_and_validate_grounded_output(
    content: str,
    ranked: tuple[RankedEvidence, ...],
    support_spans: tuple[SupportSpan, ...] = (),
) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MultiSourceGroundingError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise MultiSourceGroundingError("root_not_object")
    evidence = {value.item.evidence_id: value.item for value in ranked}
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise MultiSourceGroundingError("claims_missing")
    span_map = {span.span_id: span for span in support_spans}
    claims: list[GroundedSourceClaim] = []
    for index, raw in enumerate(raw_claims[:6]):
        if not isinstance(raw, dict):
            raise MultiSourceGroundingError("claim_not_object")
        text = _text(raw.get("text"), 1600)
        if not text:
            raise MultiSourceGroundingError("unsupported_claim")
        supports = _supports_for_claim(raw, evidence, span_map)
        supports = _augment_numeric_supports(text, supports, span_map, evidence)
        _validate_claim_numbers(text, supports)
        kind = "answer" if index == 0 else _claim_kind(supports)
        claims.append(GroundedSourceClaim(kind=kind, text=text, supports=supports))
    direct = claims[0].text
    return {"direct_answer": direct, "claims": tuple(claims)}


def _supports_for_claim(
    raw: dict[str, Any],
    evidence: dict[str, EvidenceItem],
    span_map: dict[str, SupportSpan],
) -> tuple[SourceSupport, ...]:
    if span_map:
        raw_ids = raw.get("support_ids")
        # A single string is a benign representation difference; canonicalize it
        # locally rather than spending a repair call. Semantic violations still fail.
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise MultiSourceGroundingError("support_ids_missing")
        supports: list[SourceSupport] = []
        seen: set[str] = set()
        for raw_id in raw_ids[:4]:
            if not isinstance(raw_id, str):
                raise MultiSourceGroundingError("support_id_not_string")
            span = span_map.get(raw_id.strip())
            if span is None:
                raise MultiSourceGroundingError("unknown_support_span")
            item = evidence.get(span.evidence_id)
            if item is None:
                raise MultiSourceGroundingError("unknown_evidence_id")
            if not quote_supported_by_item(span.quote, item):
                raise MultiSourceGroundingError("quote_not_in_evidence")
            if span.span_id in seen:
                continue
            seen.add(span.span_id)
            supports.append(SourceSupport(
                evidence_id=item.evidence_id,
                source_type=str(item.source_type),
                source_ref=item.source_ref,
                quote=span.quote,
                support_class="application_selected_verbatim_span",
            ))
        if not supports:
            raise MultiSourceGroundingError("unsupported_claim")
        return tuple(supports)
    # Compatibility for deterministic fixtures and old cached parser tests. New
    # synthesis never asks the model to manufacture quote text.
    supports_raw = raw.get("supports")
    if not isinstance(supports_raw, list) or not supports_raw:
        raise MultiSourceGroundingError("unsupported_claim")
    supports: list[SourceSupport] = []
    for support_raw in supports_raw[:4]:
        if not isinstance(support_raw, dict):
            raise MultiSourceGroundingError("support_not_object")
        evidence_id = _text(support_raw.get("evidence_id"), 160)
        quote = _text(support_raw.get("quote"), 500)
        if not evidence_id or evidence_id not in evidence or len(normalize_text(quote)) < 8:
            raise MultiSourceGroundingError("unknown_or_short_support")
        item = evidence[evidence_id]
        if not quote_supported_by_item(quote, item):
            raise MultiSourceGroundingError("quote_not_in_evidence")
        supports.append(SourceSupport(
            evidence_id=evidence_id,
            source_type=str(item.source_type),
            source_ref=item.source_ref,
            quote=quote,
            support_class="exact_or_normalized_excerpt",
        ))
    return tuple(supports)


def _claim_kind(supports: tuple[SourceSupport, ...]) -> str:
    types = {support.source_type for support in supports}
    if types == {"archive"}:
        return "archive"
    if types & {"scientific", "dental_knowledge"} and not types & {"current_web", "official", "archive"}:
        return "scientific"
    if types & {"current_web", "official"} and not types & {"scientific", "dental_knowledge", "archive"}:
        return "current"
    return "finding"


def _augment_numeric_supports(
    text: str,
    supports: tuple[SourceSupport, ...],
    span_map: dict[str, SupportSpan],
    evidence: dict[str, EvidenceItem],
) -> tuple[SourceSupport, ...]:
    claim_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", normalize_text(text).casefold()))
    if not claim_numbers or not supports:
        return supports
    supported_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", normalize_text("\n".join(s.quote for s in supports)).casefold()))
    missing = claim_numbers - supported_numbers
    if not missing:
        return supports
    allowed_evidence_ids = {s.evidence_id for s in supports}
    out = list(supports)
    seen_quotes = {normalize_text(s.quote).casefold() for s in supports}
    for span in span_map.values():
        if span.evidence_id not in allowed_evidence_ids:
            continue
        nums = set(re.findall(r"\d+(?:[.,]\d+)?", normalize_text(span.quote).casefold()))
        if not (missing & nums):
            continue
        item = evidence.get(span.evidence_id)
        if item is None or not quote_supported_by_item(span.quote, item):
            continue
        if not _numeric_span_context_matches(span.quote, supports):
            continue
        norm_quote = normalize_text(span.quote).casefold()
        if norm_quote in seen_quotes:
            continue
        out.append(SourceSupport(span.evidence_id, str(item.source_type), item.source_ref, span.quote, "application_attached_numeric_span"))
        seen_quotes.add(norm_quote)
        supported_numbers |= nums
        missing = claim_numbers - supported_numbers
        if not missing or len(out) >= 4:
            break
    return tuple(out)


def _numeric_span_context_matches(quote: str, supports: tuple[SourceSupport, ...]) -> bool:
    candidate = _significant_tokens(quote)
    if not candidate:
        return False
    for support in supports:
        existing = _significant_tokens(support.quote)
        if len(candidate & existing) >= 2:
            return True
    return False


def _significant_tokens(text: str) -> set[str]:
    normalized = normalize_text(text).casefold()
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "was", "were", "are", "has", "had",
        "در", "از", "به", "با", "برای", "که", "این", "آن", "است", "بود", "شد", "شده", "یک", "های",
    }
    return {
        token for token in re.findall(r"[^\W\d_]{3,}", normalized, flags=re.UNICODE)
        if token not in stop
    }


def _validate_claim_numbers(text: str, supports: tuple[SourceSupport, ...]) -> None:
    """Numbers are high-risk factual tokens and may not come from model memory."""
    normalized_claim = normalize_text(text).casefold()
    normalized_support = normalize_text("\n".join(support.quote for support in supports)).casefold()
    claim_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", normalized_claim))
    support_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", normalized_support))
    if not claim_numbers.issubset(support_numbers):
        raise MultiSourceGroundingError("unsupported_numeric_token")


def quote_supported_by_item(quote: str, item: EvidenceItem) -> bool:
    haystack = normalize_text("\n".join(value for value in (item.title, item.text, item.context) if value)).casefold()
    needle = normalize_text(quote).casefold()
    return bool(needle and needle in haystack)


def external_source_records(claims: tuple[GroundedSourceClaim, ...], ranked: tuple[RankedEvidence, ...]) -> tuple[dict[str, Any], ...]:
    by_id = {value.item.evidence_id: value.item for value in ranked}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in claims:
        for support in claim.supports:
            item = by_id.get(support.evidence_id)
            if item is None or str(item.source_type) == "archive" or item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            out.append({
                "evidence_id": item.evidence_id,
                "source_type": str(item.source_type),
                "title": item.title,
                "source_name": item.source_name,
                "source_ref": item.source_ref,
                "url": item.url,
                "timestamp": item.timestamp,
                "publication_year": item.publication_year,
                "publication_type": item.publication_type,
                "author_or_org": item.author_or_org,
                "trust_tier": item.trust_tier,
            })
    return tuple(out)


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def _optional_text(value: Any, limit: int) -> str | None:
    text = _text(value, limit)
    return text or None


def _strings(value: Any, limit: int, char_limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value[:limit] if (text := _text(item, char_limit)))

__all__ = [
    "SupportSpan",
    "MultiSourceGroundingError",
    "parse_and_validate_grounded_output",
    "quote_supported_by_item",
    "external_source_records",
]
