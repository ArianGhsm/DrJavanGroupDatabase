from __future__ import annotations

import json
from typing import Any

from .models import AnswerResult, EvidencePack


class ModelOutputError(ValueError):
    pass


class CitationValidationError(ModelOutputError):
    pass


_ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def parse_json_object(content: str) -> dict[str, Any]:
    """Local-only repair: strip fences / surrounding chatter and parse one object.

    No extra model call is made for repair; malformed content fails closed.
    """
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
    """Normalize harmless JSON-shape variations without inventing facts.

    Grounding is still enforced later against the evidence pack. This function
    only converts semantically equivalent scalar/list representations and fills
    optional presentation fields with empty values.
    """
    out = dict(payload)
    for name in ("key_findings", "disagreements", "source_refs"):
        value = out.get(name)
        if value is None:
            out[name] = []
        elif isinstance(value, str):
            out[name] = [value]

    ids = out.get("cited_message_ids")
    if ids is None:
        out["cited_message_ids"] = []
    elif isinstance(ids, (int, str)) and not isinstance(ids, bool):
        out["cited_message_ids"] = [ids]
    if isinstance(out.get("cited_message_ids"), list):
        normalized_ids: list[object] = []
        for value in out["cited_message_ids"]:
            if isinstance(value, str) and value.strip().isdigit():
                normalized_ids.append(int(value.strip()))
            else:
                normalized_ids.append(value)
        out["cited_message_ids"] = normalized_ids

    insufficient = out.get("insufficient_evidence")
    if isinstance(insufficient, str):
        folded = insufficient.strip().casefold()
        if folded == "true":
            out["insufficient_evidence"] = True
        elif folded == "false":
            out["insufficient_evidence"] = False

    out.setdefault("key_findings", [])
    out.setdefault("disagreements", [])
    out.setdefault("practical_conclusion", None)
    out.setdefault("confidence_reason", "")
    out.setdefault("safety_note_if_needed", None)
    return out


def validate_answer_payload(payload: dict[str, Any], pack: EvidencePack, *, question: str) -> AnswerResult:
    payload = normalize_answer_payload(payload)
    # Only fields that are essential to factual grounding are hard-required.
    # Counts and safety text are computed deterministically by the application.
    required = {
        "direct_answer", "confidence", "cited_message_ids", "source_refs", "insufficient_evidence",
    }
    missing = required - set(payload)
    if missing:
        raise ModelOutputError("model output is missing required fields")

    direct_answer = _string(payload["direct_answer"], "direct_answer")
    key_findings = _string_list(payload["key_findings"], "key_findings", max_items=12)
    disagreements = _string_list(payload["disagreements"], "disagreements", max_items=12)
    practical = _optional_string(payload["practical_conclusion"], "practical_conclusion")
    confidence = _string(payload["confidence"], "confidence").casefold()
    if confidence not in _ALLOWED_CONFIDENCE:
        raise ModelOutputError("confidence must be high, medium or low")
    confidence_reason = _optional_string(payload.get("confidence_reason"), "confidence_reason") or "میزان اتکا بر اساس شواهد ارجاع‌شده تعیین شد."
    insufficient = payload["insufficient_evidence"]
    if not isinstance(insufficient, bool):
        raise ModelOutputError("insufficient_evidence must be boolean")
    safety = _optional_string(payload.get("safety_note_if_needed"), "safety_note_if_needed")

    cited_ids = _int_list(payload["cited_message_ids"], "cited_message_ids", max_items=50)
    source_refs = _string_list(payload["source_refs"], "source_refs", max_items=50)
    invalid_ids = [value for value in cited_ids if value not in pack.message_ids]
    invalid_refs = [value for value in source_refs if value not in pack.source_refs]
    if invalid_ids or invalid_refs:
        raise CitationValidationError("model cited evidence that was not supplied")
    if not insufficient and not (cited_ids or source_refs):
        raise CitationValidationError("supported answer must cite supplied evidence")

    by_id = {item.message_id: item for item in pack.messages if item.message_id is not None}
    by_ref = {item.source_ref: item for item in pack.messages}
    used_refs: set[str] = set(source_refs)
    for message_id in cited_ids:
        item = by_id.get(message_id)
        if item:
            used_refs.add(item.source_ref)
    used_items = [by_ref[ref] for ref in sorted(used_refs) if ref in by_ref]
    authors = {item.author.strip().casefold() for item in used_items if item.author and item.author.strip()}
    evidence_used = len(used_items)
    independent_authors = len(authors)

    # Deterministic confidence guardrails; model cannot claim high confidence
    # from a single source or hide explicit disagreement.
    if insufficient:
        confidence = "low"
    elif evidence_used <= 1 or independent_authors <= 1:
        confidence = "low"
        if not confidence_reason.strip():
            confidence_reason = "شواهد مستقل محدود است."
    elif disagreements and confidence == "high":
        confidence = "medium"

    if _looks_clinically_consequential(question) and not safety:
        safety = "این پاسخ جمع‌بندی پیام‌های آرشیو است و جایگزین گایدلاین، ارزیابی بیمار یا قضاوت بالینی نیست."

    return AnswerResult(
        direct_answer=direct_answer,
        key_findings=key_findings,
        disagreements=disagreements,
        practical_conclusion=practical,
        confidence=confidence,
        confidence_reason=confidence_reason,
        cited_message_ids=tuple(dict.fromkeys(cited_ids)),
        source_refs=tuple(dict.fromkeys(source_refs)),
        evidence_used_count=evidence_used,
        independent_authors_count=independent_authors,
        insufficient_evidence=insufficient,
        safety_note_if_needed=safety,
        evidence_pack_estimated_tokens=pack.estimated_tokens,
    )


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


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelOutputError(f"{name} must be string or null")
    text = value.strip()
    return text or None


def _string_list(value: Any, name: str, *, max_items: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ModelOutputError(f"{name} must be a list")
    out: list[str] = []
    for item in value[:max_items]:
        if not isinstance(item, str):
            raise ModelOutputError(f"{name} must contain strings")
        text = item.strip()
        if text:
            out.append(text)
    return tuple(out)


def _int_list(value: Any, name: str, *, max_items: int) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ModelOutputError(f"{name} must be a list")
    out: list[int] = []
    for item in value[:max_items]:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ModelOutputError(f"{name} must contain integers")
        out.append(item)
    return tuple(out)


def _looks_clinically_consequential(question: str) -> bool:
    q = question.casefold()
    terms = (
        "بیمار", "درمان", "دارو", "دوز", "تشخیص", "جراحی", "اندو", "عصب", "rct", "implant",
        "ایمپلنت", "پریو", "کشیدن", "تجویز", "infection", "diagnosis", "treatment", "dose",
    )
    return any(term in q for term in terms)
