from __future__ import annotations

from dataclasses import replace
import math
import re
from typing import Sequence

from drjavanbot.normalization import normalize_text
from drjavanbot.search import EvidenceCandidate
from .config import AIConfig, EvidenceBudget
from .models import EvidenceMessage, EvidencePack

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_INVITE_RE = re.compile(r"https?://t\.me/(?:joinchat/|\+)[^\s<]+", re.I)


def estimate_tokens(text: str) -> int:
    """Conservative multilingual estimate used only for hard local budgeting."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3.0))


def build_evidence_pack(
    question: str,
    candidates: Sequence[EvidenceCandidate],
    config: AIConfig,
) -> EvidencePack:
    budget = config.budget_for(question)
    max_tokens = min(budget.max_evidence_tokens, config.hard_evidence_tokens)
    max_messages = min(budget.max_messages, config.hard_messages)
    items: list[EvidenceMessage] = []
    seen_refs: set[str] = set()
    used_tokens = estimate_tokens(question) + 120  # envelope/schema allowance

    for candidate in candidates:
        if len(items) >= max_messages:
            break
        primary = _message_from_candidate(candidate, budget)
        primary, cost = _fit_item(primary, max_tokens - used_tokens, budget.per_primary_chars)
        if primary is None:
            continue
        if primary.source_ref not in seen_refs:
            items.append(primary)
            seen_refs.add(primary.source_ref)
            used_tokens += cost

        for context_record in candidate.context:
            if len(items) >= max_messages:
                break
            ref = context_record.source_locator
            if not ref or ref in seen_refs:
                continue
            context = EvidenceMessage(
                source_ref=ref,
                message_id=context_record.message_id,
                author=context_record.author,
                datetime=_datetime_label(context_record.datetime, context_record.datetime_raw),
                source_file=context_record.source_file,
                text=_redact_obvious_pii(context_record.text_raw),
                role="context",
                parent_source_ref=primary.source_ref,
            )
            context, cost = _fit_item(context, max_tokens - used_tokens, budget.per_context_chars)
            if context is None:
                break
            items.append(context)
            seen_refs.add(context.source_ref)
            used_tokens += cost

    return EvidencePack(
        question=question,
        normalized_question=normalize_text(question),
        budget_name=budget.name,
        estimated_tokens=min(used_tokens, max_tokens),
        messages=tuple(items),
    )


def assess_retrieval(candidates: Sequence[EvidenceCandidate]) -> tuple[bool, str]:
    """Return (needs_ai_query_expansion, reason). Precision-biased and deterministic."""
    if not candidates:
        return True, "no_candidates"
    top = candidates[0]
    reasons = set(top.match_reasons)
    if top.local_score >= 5.5 and ("exact_phrase" in reasons or "normalized_tokens" in reasons):
        return False, "strong_top_match"
    strong = [c for c in candidates[:8] if c.local_score >= 3.0 and set(c.match_reasons) & {"exact_phrase", "normalized_tokens", "synonym"}]
    independent = {c.message.author_normalized for c in strong if c.message.author_normalized}
    if len(strong) >= 3 and len(independent) >= 2:
        return False, "multiple_strong_matches"
    if reasons and reasons <= {"fuzzy", "fts_bm25"}:
        return True, "weak_lexical_only"
    if len(candidates) <= 1 and top.local_score < 5.0:
        return True, "single_weak_candidate"
    return False, "adequate_local_retrieval"


def _message_from_candidate(candidate: EvidenceCandidate, budget: EvidenceBudget) -> EvidenceMessage:
    record = candidate.message
    return EvidenceMessage(
        source_ref=record.source_locator,
        message_id=record.message_id,
        author=record.author,
        datetime=_datetime_label(record.datetime, record.datetime_raw),
        source_file=record.source_file,
        text=_redact_obvious_pii(record.text_raw),
        role="evidence",
        local_score=candidate.local_score,
        matched_terms=candidate.matched_terms,
        match_reasons=candidate.match_reasons,
    )


def _fit_item(item: EvidenceMessage, remaining_tokens: int, preferred_chars: int) -> tuple[EvidenceMessage | None, int]:
    if remaining_tokens < 80 or not item.source_ref:
        return None, 0
    metadata_tokens = estimate_tokens(item.source_ref + (item.author or "") + item.source_file) + 70
    text_tokens_available = remaining_tokens - metadata_tokens
    if text_tokens_available < 20:
        return None, 0
    text = item.text.strip()
    cap = min(preferred_chars, max(60, text_tokens_available * 3))
    text = _truncate_middle(text, cap)
    fitted = replace(item, text=text)
    cost = metadata_tokens + estimate_tokens(text)
    if cost > remaining_tokens:
        # ceil() in token estimation can leave a tiny rounding overrun.
        shrink = max(20, (remaining_tokens - metadata_tokens - 1) * 3)
        fitted = replace(fitted, text=_truncate_middle(text, shrink))
        cost = metadata_tokens + estimate_tokens(fitted.text)
    if cost > remaining_tokens:
        return None, 0
    return fitted, cost


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 40:
        return text[:limit]
    head = (limit - 3) // 2
    tail = limit - 3 - head
    return text[:head].rstrip() + " … " + text[-tail:].lstrip()


def _redact_obvious_pii(text: str) -> str:
    text = _PHONE_RE.sub("[شماره تماس حذف شد]", text)
    text = _EMAIL_RE.sub("[ایمیل حذف شد]", text)
    return _INVITE_RE.sub("[لینک دعوت حذف شد]", text)


def _datetime_label(value, raw: str | None) -> str | None:
    if value is not None:
        return value.isoformat()
    return raw
