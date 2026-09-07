from __future__ import annotations

import json
from typing import Any

from drjavanbot.normalization import normalize_text
from .models import AnswerResult, ClaimSupport, EvidencePack, GroundedClaim


class ModelOutputError(ValueError):
    pass


class CitationValidationError(ModelOutputError):
    pass


_ALLOWED_CLAIM_KINDS = {"answer", "finding", "disagreement", "conclusion"}
_MAX_CLAIMS = 10
_MAX_SUPPORTS_PER_CLAIM = 4
_MAX_QUOTE_CHARS = 360


def parse_json_object(content: str) -> dict[str, Any]:
    """Local-only repair: strip fences / surrounding chatter and parse one object."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ModelOutputError("model output is not a JSON object")
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ModelOutputError("model output contains malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelOutputError("model output must be a JSON object")
    return parsed


def normalize_answer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize harmless structured-output variations without inventing support."""
    out = dict(payload)
    insufficient = out.get("insufficient_evidence")
    if isinstance(insufficient, str):
        folded = insufficient.strip().casefold()
        if folded == "true":
            out["insufficient_evidence"] = True
        elif folded == "false":
            out["insufficient_evidence"] = False

    claims = out.get("claims")
    if claims is None:
        out["claims"] = []
    elif isinstance(claims, dict):
        out["claims"] = [claims]
    return out


def validate_answer_payload(payload: dict[str, Any], pack: EvidencePack, *, question: str) -> AnswerResult:
    """Validate every displayable factual claim against an exact archive excerpt.

    The model does not get to assert global citations and then write arbitrary
    prose. Every supported claim must name a supplied message and quote text that
    is deterministically present in that exact evidence message. Aggregate source
    IDs, source refs, evidence counts and confidence are all derived locally.
    """
    payload = normalize_answer_payload(payload)
    if "insufficient_evidence" not in payload:
        raise ModelOutputError("model output is missing insufficient_evidence")
    insufficient = payload["insufficient_evidence"]
    if not isinstance(insufficient, bool):
        raise ModelOutputError("insufficient_evidence must be boolean")

    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        raise ModelOutputError("claims must be a list")
    if len(raw_claims) > _MAX_CLAIMS:
        raise ModelOutputError("too many claims")

    if insufficient:
        if raw_claims:
            raise ModelOutputError("insufficient answer must not contain factual claims")
        return _insufficient_archive_answer(pack, question=question)

    claims = tuple(_validate_claim(item, pack, question=question) for item in raw_claims)
    if not claims:
        raise CitationValidationError("supported answer requires at least one grounded claim")
    if not any(item.kind == "answer" for item in claims):
        raise CitationValidationError("supported answer requires an answer claim")

    cited_ids: list[int] = []
    source_refs: list[str] = []
    for claim in claims:
        for support in claim.supports:
            if support.message_id not in cited_ids:
                cited_ids.append(support.message_id)
            if support.source_ref not in source_refs:
                source_refs.append(support.source_ref)

    by_ref = {item.source_ref: item for item in pack.messages}
    used_items = [by_ref[ref] for ref in source_refs if ref in by_ref]
    authors = {
        item.author.strip().casefold()
        for item in used_items
        if item.author and item.author.strip()
    }
    evidence_used = len(used_items)
    independent_authors = len(authors)
    disagreements = tuple(item.text for item in claims if item.kind == "disagreement")
    confidence, confidence_reason = _archive_coverage_confidence(
        evidence_used=evidence_used,
        independent_authors=independent_authors,
        has_disagreement=bool(disagreements),
    )

    answer_texts = [item.text for item in claims if item.kind == "answer"]
    finding_texts = tuple(item.text for item in claims if item.kind == "finding")
    conclusion_texts = [item.text for item in claims if item.kind == "conclusion"]
    safety = _archive_safety_note(question)

    return AnswerResult(
        direct_answer=" ".join(answer_texts),
        key_findings=finding_texts,
        disagreements=disagreements,
        practical_conclusion=" ".join(conclusion_texts) if conclusion_texts else None,
        confidence=confidence,
        confidence_reason=confidence_reason,
        cited_message_ids=tuple(cited_ids),
        source_refs=tuple(source_refs),
        evidence_used_count=evidence_used,
        independent_authors_count=independent_authors,
        insufficient_evidence=False,
        safety_note_if_needed=safety,
        grounded_claims=claims,
        evidence_pack_estimated_tokens=pack.estimated_tokens,
    )


def _validate_claim(value: Any, pack: EvidencePack, *, question: str) -> GroundedClaim:
    if not isinstance(value, dict):
        raise ModelOutputError("each claim must be an object")
    kind = _string(value.get("kind"), "claim.kind").casefold()
    if kind not in _ALLOWED_CLAIM_KINDS:
        raise ModelOutputError("claim.kind is invalid")
    text = _string(value.get("text"), "claim.text")
    if len(text) > 900:
        raise ModelOutputError("claim.text is too long")
    raw_supports = value.get("supports")
    if not isinstance(raw_supports, list) or not raw_supports:
        raise CitationValidationError("every factual claim requires archive support")
    if len(raw_supports) > _MAX_SUPPORTS_PER_CLAIM:
        raise ModelOutputError("claim has too many supports")

    supports: list[ClaimSupport] = []
    seen: set[tuple[int, str]] = set()
    for raw_support in raw_supports:
        support = _validate_support(raw_support, pack)
        key = (support.message_id, normalize_text(support.quote))
        if key in seen:
            continue
        seen.add(key)
        supports.append(support)
    if not supports:
        raise CitationValidationError("claim has no valid archive support")

    _validate_technical_tokens(text, supports, question=question)
    return GroundedClaim(kind=kind, text=text, supports=tuple(supports))


def _validate_support(value: Any, pack: EvidencePack) -> ClaimSupport:
    if not isinstance(value, dict):
        raise ModelOutputError("claim support must be an object")
    raw_id = value.get("message_id")
    if isinstance(raw_id, str) and raw_id.strip().isdigit():
        raw_id = int(raw_id.strip())
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise ModelOutputError("support.message_id must be an integer")
    quote = _string(value.get("quote"), "support.quote")
    if len(quote) > _MAX_QUOTE_CHARS:
        raise ModelOutputError("support.quote is too long")

    candidates = [item for item in pack.messages if item.message_id == raw_id]
    if not candidates:
        raise CitationValidationError("claim support references a message that was not supplied")
    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        raise CitationValidationError("support quote is empty after normalization")
    matching = [item for item in candidates if normalized_quote in normalize_text(item.text)]
    if not matching:
        raise CitationValidationError("support quote is not present in the cited archive message")
    item = matching[0]
    return ClaimSupport(message_id=raw_id, source_ref=item.source_ref, quote=quote.strip())


def _validate_technical_tokens(text: str, supports: list[ClaimSupport], *, question: str) -> None:
    """Reject invented Latin/product/number tokens absent from question and support.

    Tokens are compared exactly after the same Persian/English normalization used
    by retrieval. This prevents substring loopholes (for example 250 vs 2500) and
    covers single-character model components and single-digit numbers too.
    """
    permitted = _technical_tokens(question + " " + " ".join(item.quote for item in supports))
    introduced = _technical_tokens(text) - permitted
    if introduced:
        raise CitationValidationError("claim introduced a technical/product token absent from its archive support")


def _technical_tokens(value: str) -> set[str]:
    out: set[str] = set()
    for token in normalize_text(value).split():
        has_ascii_alpha = any("a" <= ch <= "z" for ch in token)
        has_digit = any(ch.isdigit() for ch in token)
        if has_ascii_alpha or has_digit:
            out.add(token)
    return out


def _archive_coverage_confidence(*, evidence_used: int, independent_authors: int, has_disagreement: bool) -> tuple[str, str]:
    if evidence_used >= 4 and independent_authors >= 3 and not has_disagreement:
        level = "high"
    elif evidence_used >= 2 and independent_authors >= 2:
        level = "medium"
    else:
        level = "low"
    reason = f"پشتیبانی آرشیوی: {evidence_used} پیام از {independent_authors} نویسنده مستقل."
    if has_disagreement:
        reason += " در پیام‌های بازیابی‌شده اختلاف‌نظر هم وجود دارد."
    return level, reason


def _insufficient_archive_answer(pack: EvidencePack, *, question: str) -> AnswerResult:
    return AnswerResult(
        direct_answer="در پیام‌های گروه، شواهد کافی برای پاسخ به این سؤال پیدا نشد.",
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason="شواهد قابل استناد کافی در آرشیو گروه پیدا نشد.",
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=_archive_safety_note(question),
        grounded_claims=(),
        evidence_pack_estimated_tokens=pack.estimated_tokens,
    )


def _archive_safety_note(question: str) -> str | None:
    if not _looks_clinically_consequential(question):
        return None
    return "این فقط جمع‌بندی پیام‌های گروه است؛ گایدلاین یا توصیه مستقل هوش مصنوعی نیست."


def parse_query_variants(content: str, *, original: str, limit: int = 6) -> tuple[str, ...]:
    payload = parse_json_object(content)
    values = payload.get("variants")
    if not isinstance(values, list):
        raise ModelOutputError("query expansion output is missing variants")
    original_fold = original.strip().casefold()
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = " ".join(value.strip().split())
        if not value or len(value) > 100 or value.casefold() == original_fold:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return tuple(out)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ModelOutputError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ModelOutputError(f"{name} cannot be empty")
    return text


def _looks_clinically_consequential(question: str) -> bool:
    q = question.casefold()
    terms = (
        "بیمار", "درمان", "دارو", "دوز", "تشخیص", "جراحی", "اندو", "عصب", "rct", "implant",
        "ایمپلنت", "پریو", "کشیدن", "تجویز", "infection", "diagnosis", "treatment", "dose",
    )
    return any(term in q for term in terms)
