from __future__ import annotations

from dataclasses import replace
import json
import re
import time

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import AnswerResult, ClaimSupport, GroundedClaim, GroundedSourceClaim, SourceSupport
from drjavanbot.ai.provider import AvalAIClient, ProviderError
from drjavanbot.ai.provider_v4 import DeepSeekV4AvalAIClient
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.normalization import normalize_text
from .fusion import RankedEvidence
from .grounding import SupportSpan, MultiSourceGroundingError, external_source_records, parse_and_validate_grounded_output
from .model_policy import ModelPolicy, ModelStage
from .models import QuestionUnderstanding, RequestedFactCoverage, SourceRoute, SourceType

_SYNTHESIS_SCHEMA_VERSION = "grounded-synthesis-v2.1"
_MAX_EVIDENCE = 6
_MAX_SPANS_PER_ITEM = 4
_MAX_SPAN_CHARS = 420
_MAX_ITEM_CHARS = 1500

_SYSTEM_PROMPT = f'''You synthesize a grounded dental answer from prevalidated evidence spans.
Return JSON only. Schema: {_SYNTHESIS_SCHEMA_VERSION}.
Output ONLY this compact shape: {{"claims":[{{"text":"...","support_ids":["s001"]}}]}}.
Use 1-4 claim objects. claims[0] is the direct answer. Do not output direct_answer, quotes, source labels, confidence, counts, limitations, or other presentation fields.
Every factual detail in each claim must be supported by the selected support_ids. support_ids must be copied exactly from supplied spans; never invent an ID.
Do not use model memory as factual authority. Never turn Telegram/community opinion into scientific truth or a current estimate into a timeless fact.
For explicit hybrid requests, include grounded claims covering every required source class. For salary/cost, the direct answer must include the supported amount/range, currency/unit, evidence year/date, and cautious wording tied to the source.
Answer in the user's language. Keep claims concise. Do not expose hidden reasoning.'''


class GroundedMultiSourceSynthesizer:
    def __init__(self, *, api_key: str, base_config: AIConfig, model_policy: ModelPolicy, telemetry: TelemetryStore | None = None) -> None:
        self.api_key = api_key
        self.base_config = base_config
        self.model_policy = model_policy
        self.telemetry = telemetry

    def synthesize(
        self,
        understanding: QuestionUnderstanding,
        route: SourceRoute,
        ranked: tuple[RankedEvidence, ...],
        coverage: RequestedFactCoverage,
    ) -> AnswerResult:
        if not coverage.answerable or not ranked:
            return _insufficient(coverage.reason_code, source_mode=_source_mode(route))
        if _use_deterministic_archive_path(understanding, route):
            return _deterministic_archive_answer(understanding, route, ranked, coverage)

        decision = self.model_policy.select(
            ModelStage.SYNTHESIS,
            ambiguity=any(item.resolved_to is None for item in understanding.ambiguity),
            facet_count=len(understanding.facets),
            mixed_language=understanding.language_profile == "mixed",
            high_stakes=understanding.safety_class in {"medication", "high_stakes"},
        )
        evidence_payload, support_spans = _evidence_payload(ranked, route, facets=understanding.facets)
        user_payload = json.dumps({
            "question": understanding.normalized_question,
            "intent": str(understanding.intent),
            "facets": list(understanding.facets),
            "geography": {"country_code": understanding.geography.country_code, "label": understanding.geography.label},
            "freshness": str(understanding.freshness),
            "required_sources": [str(value) for value in route.required_sources],
            "evidence": evidence_payload,
        }, ensure_ascii=False, separators=(",", ":"))
        calls = 0
        last_error = "structured_output_failure"
        result = None
        for attempt in range(2):
            calls += 1
            config = replace(self.base_config, model=decision.model, reasoning_effort=decision.reasoning_effort)
            client = AvalAIClient(config) if decision.thinking_enabled else DeepSeekV4AvalAIClient(config)
            max_tokens = max(decision.max_output_tokens, 700 if attempt == 0 else 900)
            prompt = user_payload
            if attempt:
                prompt += (
                    "\nThe previous compact output failed local validation with reason="
                    + last_error
                    + ". Repair only the compact claims/support_ids JSON. Do not add fields."
                )
            started = time.perf_counter()
            try:
                result = client.chat_json(
                    api_key=self.api_key,
                    request_type="multisource_synthesis",
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    max_output_tokens=max_tokens,
                )
                if str(result.finish_reason or "").casefold() in {"length", "max_tokens", "token_limit"}:
                    raise MultiSourceGroundingError("output_truncated")
                parsed = parse_and_validate_grounded_output(result.content, ranked, support_spans)
                _validate_required_source_claims(parsed, route)
                parsed = _canonicalize_claim_order(parsed, understanding, route)
                _validate_requested_answer_shape(parsed, understanding, ranked)
                parsed = _canonicalize_presentation(parsed, understanding, route)
                self._record(result, True, calls, len(ranked), None)
                return _answer_from_parsed(parsed, ranked, route, coverage, calls)
            except (MultiSourceGroundingError, json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = str(exc) or type(exc).__name__
                self._record(result, False, calls, len(ranked), last_error, fallback_latency=(time.perf_counter() - started) * 1000)
                continue
            except ProviderError as exc:
                self._record(result, False, calls, len(ranked), type(exc).__name__, fallback_latency=(time.perf_counter() - started) * 1000)
                return _insufficient("provider_failure", source_mode=_source_mode(route), ai_calls=calls)
        return _insufficient("grounding_validation_failed:" + last_error, source_mode=_source_mode(route), ai_calls=calls)

    def _record(self, result, success: bool, call: int, evidence_count: int, reason: str | None, *, fallback_latency: float = 0.0) -> None:
        if self.telemetry is None:
            return
        self.telemetry.record(
            request_type="multisource_synthesis",
            stage="synthesis",
            result_class="success" if success else "structured_output_failure",
            reason_code=reason,
            logical_call=call,
            attempt=call,
            evidence_count=evidence_count,
            finish_reason=(result.finish_reason if result else None),
            model=(result.model if result else self.base_config.model),
            latency_ms=(result.latency_ms if result else fallback_latency),
            success=success,
            usage=(result.usage if result else None),
            error_class=None if success else "GroundingValidationError",
        )


def _use_deterministic_archive_path(understanding: QuestionUnderstanding, route: SourceRoute) -> bool:
    required = {str(value) for value in route.required_sources}
    return (
        required == {SourceType.ARCHIVE}
        and len(understanding.facets) <= 1
        and str(understanding.safety_class) not in {"medication", "high_stakes"}
        and not understanding.ambiguity
    )

def _deterministic_archive_answer(
    understanding: QuestionUnderstanding,
    route: SourceRoute,
    ranked: tuple[RankedEvidence, ...],
    coverage: RequestedFactCoverage,
) -> AnswerResult:
    claims: list[GroundedSourceClaim] = []
    for value in ranked:
        item = value.item
        if str(item.source_type) != SourceType.ARCHIVE:
            continue
        quote = _archive_quote(item.text or "", understanding)
        if not quote:
            continue
        support = SourceSupport(
            evidence_id=item.evidence_id,
            source_type=str(item.source_type),
            source_ref=item.source_ref,
            quote=quote,
            support_class="application_selected_verbatim_span",
        )
        kind = "answer" if not claims else "archive"
        claims.append(GroundedSourceClaim(kind=kind, text=quote, supports=(support,)))
        if len(claims) >= 3:
            break
    if not claims:
        return _insufficient("archive_direct_span_missing", source_mode="archive")
    parsed = {"direct_answer": claims[0].text, "claims": tuple(claims)}
    return _answer_from_parsed(parsed, ranked, route, coverage, 0)


def _archive_quote(raw: str, understanding: QuestionUnderstanding) -> str:
    text = raw.strip()
    if len(normalize_text(text)) < 12:
        return ""
    markers = _topic_markers(understanding)
    sentences = _sentence_spans(raw)
    if markers:
        matching = [span for span in sentences if any(marker in normalize_text(span).casefold() for marker in markers)]
        if matching:
            return matching[0][:_MAX_SPAN_CHARS].strip()
    if sentences:
        return sentences[0][:_MAX_SPAN_CHARS].strip()
    return text[:_MAX_SPAN_CHARS].strip()

def _evidence_payload(
    ranked: tuple[RankedEvidence, ...],
    route: SourceRoute,
    *,
    facets: tuple[str, ...] = (),
) -> tuple[list[dict], tuple[SupportSpan, ...]]:
    selected = _selected_ranked(ranked, route)
    payload: list[dict] = []
    support_spans: list[SupportSpan] = []
    span_number = 1
    for value in selected:
        item = value.item
        spans = _candidate_support_spans(item, facets)
        if not spans:
            continue
        span_payload: list[dict[str, str]] = []
        for quote in spans:
            span_id = f"s{span_number:03d}"
            span_number += 1
            support_spans.append(SupportSpan(span_id=span_id, evidence_id=item.evidence_id, quote=quote))
            span_payload.append({"support_id": span_id, "text": quote})
        payload.append({
            "source_type": str(item.source_type),
            "spans": span_payload,
        })
    return payload, tuple(support_spans)


def _selected_ranked(ranked: tuple[RankedEvidence, ...], route: SourceRoute) -> tuple[RankedEvidence, ...]:
    selected: list[RankedEvidence] = []
    for source in route.required_sources:
        hit = next((value for value in ranked if str(value.item.source_type) == str(source)), None)
        if hit is not None and hit not in selected:
            selected.append(hit)
    for value in ranked:
        if value not in selected:
            selected.append(value)
        if len(selected) >= _MAX_EVIDENCE:
            break
    return tuple(selected[:_MAX_EVIDENCE])

def _candidate_support_spans(item, facets: tuple[str, ...]) -> tuple[str, ...]:
    raw = _support_raw_text(item)
    if not raw:
        return ()
    candidates: list[str] = []
    facet_set = set(facets)
    if facet_set & {"salary", "cost"}:
        from .current_provider import _MONEY_PATTERN
        for match in _MONEY_PATTERN.finditer(raw):
            candidates.append(_verbatim_window(raw, match.start(), limit=_MAX_SPAN_CHARS))
            if len(candidates) >= 3:
                break
    marker = _facet_marker_pattern(facet_set)
    if marker is not None:
        for match in marker.finditer(raw):
            candidates.append(_verbatim_window(raw, match.start(), limit=_MAX_SPAN_CHARS))
            if len(candidates) >= _MAX_SPANS_PER_ITEM:
                break
    candidates.extend(_sentence_spans(raw))
    if not candidates:
        candidates.append(raw[:_MAX_SPAN_CHARS])

    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        quote = candidate.strip()
        normalized = normalize_text(quote).casefold()
        if len(normalized) < 8 or normalized in seen:
            continue
        # All support text must be one contiguous substring of the real source.
        if quote not in raw:
            continue
        seen.add(normalized)
        out.append(quote)
        if len(out) >= _MAX_SPANS_PER_ITEM:
            break
    return tuple(out)


def _support_raw_text(item) -> str:
    if str(item.source_type) == SourceType.ARCHIVE:
        # An archive EvidenceItem's context may contain other message bodies while
        # its source_ref identifies only the anchor message. Never cite context as
        # though it were the anchor message.
        return item.text or ""
    return "\n".join(value for value in (item.title, item.text, item.context) if value)

def _facet_marker_pattern(facets: set[str]):
    patterns: list[str] = []
    if facets & {"prevalence", "frequency", "epidemiology"}:
        patterns.extend((r"preval", r"frequen", r"most common", r"شایع", r"شیوع", r"فراوان", r"رایج"))
    if facets & {"salary", "cost", "career"}:
        patterns.extend((r"حقوق", r"درآمد", r"دستمزد", r"تومان", r"ریال", r"salary", r"income", r"compensation", r"درصد"))
    if facets & {"treatment", "recommendation", "clinical_decision"}:
        patterns.extend((r"treat", r"recommend", r"درمان", r"پیشنهاد", r"بهتر", r"انتخاب"))
    if facets & {"diagnosis", "differential_diagnosis"}:
        patterns.extend((r"diagnos", r"differential", r"تشخیص", r"افتراق"))
    if facets & {"recurrence", "prognosis", "complication"}:
        patterns.extend((r"recurr", r"prognos", r"complication", r"عود", r"پیش.?آگهی", r"عارض"))
    if not patterns:
        patterns.extend((r"systematic", r"guideline", r"review", r"گایدلاین", r"مرور"))
    return re.compile("|".join(patterns), re.IGNORECASE) if patterns else None


def _sentence_spans(raw: str) -> tuple[str, ...]:
    spans: list[str] = []
    for match in re.finditer(r"[^\n.!?؟]{12,}(?:[.!?؟]|$)", raw):
        value = match.group(0).strip()
        if value:
            spans.append(value[:_MAX_SPAN_CHARS].strip())
        if len(spans) >= 8:
            break
    return tuple(spans)


def _verbatim_window(raw: str, center: int, *, limit: int = _MAX_ITEM_CHARS) -> str:
    before = min(320, limit // 2)
    start = max(0, int(center) - before)
    if start + limit > len(raw):
        start = max(0, len(raw) - limit)
    return raw[start:start + limit]


def _topic_markers(understanding: QuestionUnderstanding) -> tuple[str, ...]:
    out: list[str] = []
    for entity in understanding.entities:
        for value in (entity.text, entity.canonical_label, *entity.variants):
            normalized = normalize_text(value).casefold()
            if len(normalized) >= 2 and normalized not in out:
                out.append(normalized)
    return tuple(out[:16])

def _validate_required_source_claims(parsed: dict, route: SourceRoute) -> None:
    present = {
        str(support.source_type)
        for claim in tuple(parsed.get("claims") or ())
        for support in tuple(claim.supports or ())
    }
    required = {str(value) for value in route.required_sources}
    missing = required - present
    if missing:
        raise MultiSourceGroundingError("required_source_missing_from_answer")


def _canonicalize_claim_order(parsed: dict, understanding: QuestionUnderstanding, route: SourceRoute) -> dict:
    claims = list(parsed.get("claims") or ())
    if not claims:
        return parsed
    required = {str(value) for value in route.required_sources}
    facets = set(understanding.facets)
    preferred: set[str] = set()
    if facets & {"salary", "cost"} or (understanding.current_information_needed and SourceType.CURRENT_WEB in required):
        preferred = {SourceType.CURRENT_WEB, SourceType.OFFICIAL}
    elif understanding.scientific_evidence_needed or SourceType.SCIENTIFIC in required:
        preferred = {SourceType.SCIENTIFIC, SourceType.OFFICIAL, SourceType.DENTAL_KNOWLEDGE}
    elif required == {SourceType.ARCHIVE}:
        preferred = {SourceType.ARCHIVE}
    if preferred:
        index = next((i for i, claim in enumerate(claims) if {str(s.source_type) for s in claim.supports} & preferred), None)
        if index not in (None, 0):
            claims.insert(0, claims.pop(index))
    return {**parsed, "claims": tuple(claims), "direct_answer": claims[0].text}


def _canonicalize_presentation(parsed: dict, understanding: QuestionUnderstanding, route: SourceRoute) -> dict:
    claims = list(parsed.get("claims") or ())
    if not claims:
        return parsed
    facets = set(understanding.facets)
    first = claims[0]
    first_types = {str(s.source_type) for s in first.supports}
    if facets & {"salary", "cost"} and first_types & {SourceType.CURRENT_WEB, SourceType.OFFICIAL}:
        normalized = normalize_text(first.text).casefold()
        if not any(token in normalized for token in ("برآورد", "estimate", "حدود", "تقریبی", "تقریباً")):
            text = "بر اساس شواهد تاریخ‌دار بازیابی‌شده، این مقدار یک برآورد است: " + first.text
            claims[0] = GroundedSourceClaim(kind=first.kind, text=text, supports=first.supports)
    return {**parsed, "claims": tuple(claims), "direct_answer": claims[0].text}


def _validate_requested_answer_shape(parsed: dict, understanding: QuestionUnderstanding, ranked: tuple[RankedEvidence, ...]) -> None:
    claims = tuple(parsed.get("claims") or ())
    direct = str(parsed.get("direct_answer") or "")
    facets = set(understanding.facets)
    direct_types = (
        {str(support.source_type) for support in claims[0].supports}
        if claims
        else {str(value.item.source_type) for value in ranked}
    )

    if understanding.scientific_evidence_needed or facets & {"prevalence", "frequency", "epidemiology"}:
        if not direct_types & {SourceType.SCIENTIFIC, SourceType.OFFICIAL, SourceType.DENTAL_KNOWLEDGE}:
            raise MultiSourceGroundingError("direct_scientific_support_missing")
    if understanding.current_information_needed or facets & {"salary", "cost"}:
        if not direct_types & {SourceType.CURRENT_WEB, SourceType.OFFICIAL}:
            raise MultiSourceGroundingError("direct_current_support_missing")

    if facets & {"salary", "cost"}:
        normalized = normalize_text(direct).casefold()
        has_number = bool(re.search(r"\d", normalized))
        money_units = ("تومان", "ریال", "میلیون", "هزار", "irr", "rial", "toman", "million")
        has_money = any(unit in normalized for unit in money_units)
        if not (has_number and has_money):
            raise MultiSourceGroundingError("current_numeric_currency_missing")
        if not re.search(r"(?<!\d)(?:13|14|19|20)\d{2}(?!\d)", normalized):
            raise MultiSourceGroundingError("current_date_missing")

    by_id = {value.item.evidence_id: value for value in ranked}
    if understanding.facets:
        direct_scores = (
            [by_id[support.evidence_id].requested_fact_relevance for support in claims[0].supports if support.evidence_id in by_id]
            if claims
            else [value.requested_fact_relevance for value in ranked]
        )
        if not direct_scores or max(direct_scores) < 0.5:
            raise MultiSourceGroundingError("direct_requested_fact_support_missing")

def _answer_from_parsed(
    parsed: dict,
    ranked: tuple[RankedEvidence, ...],
    route: SourceRoute,
    coverage: RequestedFactCoverage,
    ai_calls: int,
) -> AnswerResult:
    claims = tuple(parsed["claims"])
    by_id = {value.item.evidence_id: value.item for value in ranked}
    cited_message_ids: list[int] = []
    source_refs: list[str] = []
    legacy_claims: list[GroundedClaim] = []
    author_keys: set[str] = set()
    used_ids: set[str] = set()

    for claim in claims:
        legacy_supports: list[ClaimSupport] = []
        for support in claim.supports:
            item = by_id.get(support.evidence_id)
            if item is None:
                continue
            used_ids.add(item.evidence_id)
            if item.source_ref not in source_refs:
                source_refs.append(item.source_ref)
            author_keys.add(item.independence_key or item.author_or_org or item.source_ref)
            if str(item.source_type) == SourceType.ARCHIVE and item.evidence_id.startswith("archive:"):
                raw = item.evidence_id.split(":", 1)[1]
                if raw.isdigit():
                    mid = int(raw)
                    if mid not in cited_message_ids:
                        cited_message_ids.append(mid)
                    legacy_supports.append(ClaimSupport(mid, item.source_ref, support.quote))
        if legacy_supports:
            legacy_claims.append(GroundedClaim(claim.kind, claim.text, tuple(legacy_supports)))

    confidence, confidence_reason = _derived_confidence(coverage, len(author_keys))
    return AnswerResult(
        direct_answer=claims[0].text,
        key_findings=tuple(claim.text for claim in claims[1:]),
        disagreements=(),
        practical_conclusion=None,
        confidence=confidence,
        confidence_reason=confidence_reason,
        cited_message_ids=tuple(cited_message_ids),
        source_refs=tuple(source_refs),
        evidence_used_count=len(used_ids),
        independent_authors_count=len(author_keys),
        insufficient_evidence=False,
        safety_note_if_needed=None,
        grounded_claims=tuple(legacy_claims),
        source_mode=_source_mode(route),
        external_sources=external_source_records(claims, ranked),
        grounded_source_claims=claims,
        limitations=(),
        ai_calls=ai_calls,
    )

def _derived_confidence(coverage: RequestedFactCoverage, independent_authors: int) -> tuple[str, str]:
    if coverage.conflicts:
        return "medium", "required_source_and_requested_fact_supported_with_source_variation"
    if independent_authors >= 2 and coverage.freshness_satisfied and coverage.source_requirement_satisfied:
        return "high", "required_source_and_requested_fact_supported_by_independent_evidence"
    return "medium", "required_source_and_requested_fact_supported"


def _compact_item_text(item, *, facets: tuple[str, ...] = ()) -> str:
    raw = "\n".join(value for value in (item.title, item.text, item.context) if value)
    if len(raw) <= _MAX_ITEM_CHARS:
        return raw
    facet_set = set(facets)
    if facet_set & {"salary", "cost"}:
        from .current_provider import _MONEY_PATTERN
        match = _MONEY_PATTERN.search(raw)
        if match:
            return _verbatim_window(raw, match.start(), limit=_MAX_ITEM_CHARS)
    marker = _facet_marker_pattern(facet_set)
    match = marker.search(raw) if marker is not None else None
    if match:
        return _verbatim_window(raw, match.start(), limit=_MAX_ITEM_CHARS)
    return raw[:_MAX_ITEM_CHARS]


def _source_mode(route: SourceRoute) -> str:
    sources = {str(item.source_type) for item in route.selected_sources if str(item.source_type) != SourceType.NONE}
    required = {str(value) for value in route.required_sources}
    if required == {SourceType.ARCHIVE}:
        return "archive"
    if SourceType.ARCHIVE in required and len(required) > 1:
        return "hybrid"
    if required & {SourceType.CURRENT_WEB, SourceType.OFFICIAL}:
        return "current" if SourceType.ARCHIVE not in required else "hybrid"
    if SourceType.SCIENTIFIC in required:
        return "scientific" if SourceType.ARCHIVE not in required else "hybrid"
    return "hybrid" if len(sources) > 1 else "scientific"

def _insufficient(reason: str, *, source_mode: str, ai_calls: int = 0) -> AnswerResult:
    labels = {
        "scientific": "شواهد علمی کافی",
        "current": "اطلاعات به‌روز و قابل اتکا",
        "hybrid": "شواهد لازم از همه منابع درخواستی",
        "archive": "شواهد کافی در آرشیو",
    }
    direct = f"برای این سؤال {labels.get(source_mode, 'شواهد کافی')} پیدا نشد؛ برای جلوگیری از حدس، پاسخ قطعی ساخته نشد."
    return AnswerResult(
        direct_answer=direct,
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason=reason,
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=None,
        source_mode=source_mode,
        limitations=(reason,),
        ai_calls=ai_calls,
    )


__all__ = ["GroundedMultiSourceSynthesizer"]
