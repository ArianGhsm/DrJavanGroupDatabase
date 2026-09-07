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
    """Pack diverse anchors first, then bounded conversation context.

    Retrieval candidates are already ranked/diversified. Context must not undo
    that work by letting the first thread consume the whole evidence budget. At
    the same time, a real archive discussion often spans several consecutive or
    reply messages where only one message repeats the searched term. Retrieval
    marks those anchors with ``discussion_window``. In that case we reserve up to
    roughly one third of message slots for conversation context, while preserving
    the same hard message/token ceilings.

    Direct reply relations and nearest/topically-linked neighbors are consumed
    before farther context. This improves conversation coverage without creating
    any extra AI call or allowing context expansion to exceed the configured
    evidence-token budget.
    """
    budget = config.budget_for(question)
    max_tokens = min(budget.max_evidence_tokens, config.hard_evidence_tokens)
    max_messages = min(budget.max_messages, config.hard_messages)
    items: list[EvidenceMessage] = []
    seen_refs: set[str] = set()
    used_tokens = estimate_tokens(question) + 120  # envelope/schema allowance

    has_context = any(candidate.context for candidate in candidates)
    discussion_heavy = sum(
        1 for candidate in candidates[:12]
        if "discussion_window" in set(candidate.match_reasons)
    ) >= 2
    if has_context and max_messages >= 4:
        if discussion_heavy:
            context_reserve = min(10, max(4, max_messages // 3))
        else:
            context_reserve = min(6, max(2, max_messages // 4))
    else:
        context_reserve = 0
    primary_soft_limit = max(1, max_messages - context_reserve)
    primary_token_soft_cap = int(max_tokens * (0.70 if discussion_heavy else 0.78)) if context_reserve else max_tokens

    selected: list[EvidenceCandidate] = []
    deferred_candidates: list[EvidenceCandidate] = []

    # Phase 1: preserve rank/diversity by admitting many primary messages before
    # any neighborhood expansion can consume the pack.
    for candidate in candidates:
        if len(items) >= primary_soft_limit or used_tokens >= primary_token_soft_cap:
            deferred_candidates.append(candidate)
            continue
        primary = _message_from_candidate(candidate, budget)
        primary, cost = _fit_item(primary, max_tokens - used_tokens, budget.per_primary_chars)
        if primary is None:
            deferred_candidates.append(candidate)
            continue
        if primary.source_ref in seen_refs:
            continue
        items.append(primary)
        seen_refs.add(primary.source_ref)
        used_tokens += cost
        selected.append(candidate)

    # Phase 2: reply parents are semantically privileged context. Add one direct
    # parent per selected primary before ordinary before/after messages.
    reply_parent_refs: set[str] = set()
    for candidate in selected:
        if len(items) >= max_messages:
            break
        parent_id = candidate.message.reply_to_message_id
        if parent_id is None:
            continue
        parent = next((ctx for ctx in candidate.context if ctx.message_id == parent_id), None)
        if parent is None or not parent.source_locator or parent.source_locator in seen_refs:
            continue
        context, cost = _fit_item(
            _context_message(parent, parent_ref=candidate.message.source_locator, role="reply_context"),
            max_tokens - used_tokens,
            budget.per_context_chars,
        )
        if context is None:
            break
        items.append(context)
        seen_refs.add(context.source_ref)
        reply_parent_refs.add(context.source_ref)
        used_tokens += cost

    # Phase 3: distribute remaining conversation context round-robin across
    # selected primaries. Each bucket is internally ordered so direct replies,
    # nearest chronological neighbors and topical continuations beat distant
    # messages when the token budget is tight.
    buckets: list[tuple[EvidenceCandidate, list]] = []
    for candidate in selected:
        remaining = [
            ctx for ctx in candidate.context
            if ctx.source_locator and ctx.source_locator not in reply_parent_refs
        ]
        remaining.sort(key=lambda record: _context_priority(record, candidate))
        if remaining:
            buckets.append((candidate, remaining))

    while buckets and len(items) < max_messages:
        progressed = False
        next_buckets: list[tuple[EvidenceCandidate, list]] = []
        for candidate, records in buckets:
            if len(items) >= max_messages:
                break
            record = records.pop(0)
            if record.source_locator in seen_refs:
                if records:
                    next_buckets.append((candidate, records))
                continue
            role = "reply_context" if record.reply_to_message_id == candidate.message.message_id else "context"
            context, cost = _fit_item(
                _context_message(record, parent_ref=candidate.message.source_locator, role=role),
                max_tokens - used_tokens,
                budget.per_context_chars,
            )
            if context is not None:
                items.append(context)
                seen_refs.add(context.source_ref)
                used_tokens += cost
                progressed = True
            if records:
                next_buckets.append((candidate, records))
        if not progressed:
            break
        buckets = next_buckets

    # Phase 4: if reserved context was not needed, use any remaining budget for
    # additional lower-ranked primaries rather than wasting capacity.
    for candidate in deferred_candidates:
        if len(items) >= max_messages:
            break
        primary = _message_from_candidate(candidate, budget)
        if primary.source_ref in seen_refs:
            continue
        primary, cost = _fit_item(primary, max_tokens - used_tokens, budget.per_primary_chars)
        if primary is None:
            break
        items.append(primary)
        seen_refs.add(primary.source_ref)
        used_tokens += cost

    return EvidencePack(
        question=question,
        normalized_question=normalize_text(question),
        budget_name=budget.name,
        estimated_tokens=min(used_tokens, max_tokens),
        messages=tuple(items),
    )


def assess_retrieval(candidates: Sequence[EvidenceCandidate]) -> tuple[bool, str]:
    """Backward-compatible legacy retrieval assessment."""
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


def _context_message(record, *, parent_ref: str, role: str) -> EvidenceMessage:
    return EvidenceMessage(
        source_ref=record.source_locator,
        message_id=record.message_id,
        author=record.author,
        datetime=_datetime_label(record.datetime, record.datetime_raw),
        source_file=record.source_file,
        text=_redact_obvious_pii(record.text_raw),
        role=role,
        parent_source_ref=parent_ref,
    )


def _context_priority(record, candidate: EvidenceCandidate) -> tuple[int, int, int, int, int]:
    anchor = candidate.message
    if record.message_id == anchor.reply_to_message_id or record.reply_to_message_id == anchor.message_id:
        relation = 0
    elif record.source_page == anchor.source_page:
        relation = 1
    else:
        relation = 2

    text = normalize_text(record.text_normalized or record.text_raw)
    topical_hits = 0
    for raw_term in candidate.matched_terms[:8]:
        term = normalize_text(raw_term)
        if term and term in text:
            topical_hits += 1

    if record.source_page == anchor.source_page:
        distance = abs(record.source_order - anchor.source_order)
    else:
        distance = 10_000 + abs(record.source_page - anchor.source_page) * 100
    return (relation, -topical_hits, distance, record.source_page, record.source_order)


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
