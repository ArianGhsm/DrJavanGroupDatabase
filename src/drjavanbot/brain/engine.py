"""The group's "brain": answer any question from the archive, with citations.

    question + recent conversation
      → UNDERSTAND  (LLM, fast): standalone question + archive-vocabulary queries
      → RETRIEVE    (local): discussion candidates, fused across phrasings
      → RERANK      (LLM, fast): which candidate discussions actually answer it
      → ANSWER      (LLM): synthesis from those discussions only, every point
                    citing message ids; ids are verified against what the model
                    was shown, so a citation can never point outside the evidence.

The LLM replaces hand-written rules (stopword lists, facet markers, anchors,
routing): it understands paraphrase and ellipsis, and it reads the evidence.
Facts come only from the archive; model knowledge is never a source.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
import re
from typing import Any, Callable, Protocol, Sequence

from drjavanbot.ai.models import AnswerResult, ClaimSupport, GroundedClaim
from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_tokens
from .redaction import redact
from .prompts import ANSWER_PROMPT, RERANK_PROMPT, UNDERSTAND_PROMPT
from .retriever import ArchiveMessage, Discussion, DiscussionRetriever

BRAIN_VERSION = "brain-1"
_LOG = logging.getLogger(__name__)
_CANDIDATES = 40
_ANSWER_DISCUSSIONS = 6
_SNIPPET_CHARS = 260
_MESSAGE_CHARS = 700
_DISCUSSION_CHARS = 4200
HOST_AUTHOR = "مهدی جوان"

ProgressCallback = Callable[[str, dict[str, object]], None]


class JSONModel(Protocol):
    """One JSON-returning LLM call. ``stage`` lets the adapter pick a model."""

    def complete_json(self, *, stage: str, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        ...


class AnswerCache(Protocol):
    def get(self, key: str) -> AnswerResult | None: ...
    def set(self, key: str, answer: AnswerResult) -> None: ...


@dataclass(frozen=True, slots=True)
class Turn:
    question: str
    standalone: str
    answer: str


@dataclass(frozen=True, slots=True)
class Understanding:
    kind: str
    standalone: str
    queries: tuple[str, ...]
    reply: str | None


@dataclass
class BrainResult:
    answer: AnswerResult
    standalone: str
    discussions: tuple[int, ...] = field(default_factory=tuple)


class ArchiveBrain:
    def __init__(
        self,
        *,
        retriever: DiscussionRetriever,
        model: JSONModel,
        cache: AnswerCache | None = None,
        index_fingerprint: str = "",
        config_signature: str = "",
    ) -> None:
        self.retriever = retriever
        self.model = model
        self.cache = cache
        self.index_fingerprint = index_fingerprint
        # Model/pipeline settings are part of an answer's identity: changing
        # the model in the owner panel must not serve the old model's answers.
        self.config_signature = config_signature

    def answer(self, question: str, *, history: Sequence[Turn] = (), progress: ProgressCallback | None = None) -> BrainResult:
        calls = 0
        _emit(progress, "planning", cached=False)
        understanding = self._understand(question, history)
        calls += 1
        if understanding.kind != "question":
            reply = understanding.reply or "سؤالتون درباره‌ی موضوعات بحث‌شده در گروه رو بفرستید تا از آرشیو جواب بدم."
            return BrainResult(_plain(reply, ai_calls=calls), understanding.standalone)

        key = self._cache_key(understanding.standalone)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                _emit(progress, "cache_hit", evidence_used=cached.evidence_used_count)
                return BrainResult(cached.with_runtime(cache_hit=True, ai_calls=calls), understanding.standalone)

        phrasings = (question, understanding.standalone, *understanding.queries)
        _emit(progress, "searching", query_count=len(phrasings), family_count=1)
        candidate_ids = self.retriever.search(phrasings, limit=_CANDIDATES)
        candidates = self.retriever.load(candidate_ids)
        if not candidates:
            _emit(progress, "no_evidence", reason="no_candidates")
            return self._store(key, BrainResult(_not_discussed(calls), understanding.standalone))
        _emit(progress, "context", candidate_count=len(candidates), author_count=_author_count(candidates),
              discussion_windows=len(candidates))

        terms = _query_terms(phrasings)
        chosen = self._rerank(understanding.standalone, candidates, terms)
        calls += 1
        if not chosen:
            _emit(progress, "no_evidence", reason="no_relevant_discussion")
            return self._store(key, BrainResult(_not_discussed(calls), understanding.standalone))

        evidence, shown = _evidence_block(chosen, terms)
        _emit(progress, "synthesizing", evidence_messages=len(shown), evidence_authors=_author_count(chosen))
        payload = self.model.complete_json(
            stage="answer", system=ANSWER_PROMPT,
            user=json.dumps({"question": understanding.standalone, "evidence": evidence}, ensure_ascii=False),
            max_tokens=1800,
        )
        calls += 1
        _emit(progress, "validating")
        result = _grounded_answer(payload, shown, calls=calls)
        _emit(progress, "done")
        return self._store(key, BrainResult(result, understanding.standalone, tuple(d.discussion_id for d in chosen)))

    # -- stages -----------------------------------------------------------
    def _understand(self, question: str, history: Sequence[Turn]) -> Understanding:
        recent = [{"question": t.question, "understood_as": t.standalone, "answer_summary": t.answer[:300]} for t in history[-3:]]
        try:
            payload = self.model.complete_json(
                stage="understand", system=UNDERSTAND_PROMPT,
                user=json.dumps({"conversation": recent, "message": question}, ensure_ascii=False),
                max_tokens=400,
            )
        except ValueError:
            payload = {}
        kind = str(payload.get("kind") or "question")
        if kind not in {"question", "smalltalk"}:
            kind = "question"
        standalone = _clean_text(payload.get("standalone")) or question.strip()
        queries = tuple(q for q in (_clean_text(v) for v in payload.get("queries") or () if isinstance(v, str)) if q)[:6]
        reply = _clean_text(payload.get("reply")) if kind == "smalltalk" else None
        return Understanding(kind, standalone, queries, reply)

    def _rerank(self, question: str, candidates: Sequence[Discussion], terms: frozenset[str]) -> tuple[Discussion, ...]:
        by_id = {d.discussion_id: d for d in candidates}
        listing = [_listing_entry(d, terms) for d in candidates]
        try:
            payload = self.model.complete_json(
                stage="rerank", system=RERANK_PROMPT,
                user=json.dumps({"question": question, "discussions": listing}, ensure_ascii=False),
                max_tokens=500,
            )
        except ValueError:
            return tuple(candidates[:3])
        chosen: list[Discussion] = []
        for item in payload.get("relevant") or ():
            if not isinstance(item, dict):
                continue
            try:
                discussion_id, relevance = int(item.get("id")), int(item.get("relevance", 0))
            except (TypeError, ValueError):
                continue
            if relevance >= 2 and discussion_id in by_id and by_id[discussion_id] not in chosen:
                chosen.append(by_id[discussion_id])
        return tuple(chosen[:_ANSWER_DISCUSSIONS])

    # -- plumbing ---------------------------------------------------------
    def _cache_key(self, standalone: str) -> str:
        raw = "\n".join((BRAIN_VERSION, self.config_signature, self.index_fingerprint, normalize_text(standalone)))
        return "brain:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _store(self, key: str, result: BrainResult) -> BrainResult:
        if self.cache is not None:
            self.cache.set(key, result.answer)
        return result


# -- evidence ------------------------------------------------------------------
def _listing_entry(discussion: Discussion, terms: frozenset[str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": discussion.discussion_id,
        "year": discussion.start.year if discussion.start else None,
        "messages": len(discussion.messages),
        "opening": _clip(redact((discussion.header or discussion.messages[0]).text), _SNIPPET_CHARS),
        "excerpt": _clip(redact(_best_message(discussion, terms).text), _SNIPPET_CHARS),
    }
    card = discussion.card
    if card is not None and card.useful:
        entry["topic"] = card.topic
        entry["summary"] = _clip(card.summary, _SNIPPET_CHARS)
    return entry


def _query_terms(phrasings: Sequence[str]) -> frozenset[str]:
    return frozenset(token for phrase in phrasings for token in informative_tokens(phrase) if len(token) >= 2)


def _relevance(message: ArchiveMessage, terms: frozenset[str]) -> int:
    padded = " " + normalize_text(message.text)
    return sum(1 for term in terms if f" {term}" in padded)


def _best_message(discussion: Discussion, terms: frozenset[str]) -> ArchiveMessage:
    return max(discussion.messages, key=lambda m: (_relevance(m, terms), len(m.text)))


def _evidence_block(discussions: Sequence[Discussion], terms: frozenset[str]) -> tuple[list[dict], dict[int, ArchiveMessage]]:
    """Compact, citable evidence. Within each discussion's budget keep the
    opening, the host's messages and the messages that match the question,
    in original order."""
    blocks: list[dict] = []
    shown: dict[int, ArchiveMessage] = {}
    for discussion in discussions:
        messages = [m for m in discussion.messages if m.text.strip() and m.message_id is not None]
        if discussion.header is not None and discussion.header.message_id is not None:
            messages.insert(0, discussion.header)
        priority = sorted(
            range(len(messages)),
            key=lambda i: (-(i == 0), -(messages[i].author == HOST_AUTHOR), -_relevance(messages[i], terms), i),
        )
        budget, keep = _DISCUSSION_CHARS, set()
        for i in priority:
            cost = min(len(messages[i].text), _MESSAGE_CHARS) + 40
            if cost > budget and keep:
                continue
            keep.add(i)
            budget -= cost
        lines = []
        for i in sorted(keep):
            message = messages[i]
            text = _clip(redact(message.text), _MESSAGE_CHARS)
            # Quotes are verified against exactly the (redacted, clipped) text shown.
            shown[int(message.message_id)] = replace(message, text=text)
            when = message.datetime.strftime("%Y-%m-%d") if message.datetime else "?"
            lines.append({"id": int(message.message_id), "author": message.author or "?", "date": when,
                          "reply_to": message.reply_to_message_id, "text": text})
        blocks.append({"discussion": discussion.discussion_id, "messages": lines})
    return blocks, shown


_ELLIPSIS_RE = re.compile(r"\s*(?:…|\.\.\.)\s*")
_MIN_QUOTE_TOKENS = 3


def _quote_in_message(quote: str, message_text: str) -> bool:
    """The quote must be the message's own words (fragments joined by … allowed)."""
    target = normalize_text(message_text)
    fragments = [normalize_text(part) for part in _ELLIPSIS_RE.split(quote or "") if normalize_text(part)]
    if not fragments or not target:
        return False
    if sum(len(f.split()) for f in fragments) < min(_MIN_QUOTE_TOKENS, len(target.split())):
        return False
    position = 0
    for fragment in fragments:
        found = target.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def _shares_content(claim: str, quotes: Sequence[str]) -> bool:
    """A claim must be about what its quotes say, not merely cite something real."""
    claim_terms = {t for t in informative_tokens(claim) if len(t) >= 3}
    quote_padded = " " + " ".join(normalize_text(q) for q in quotes)
    return any(f" {term}" in quote_padded or f" {term[:4]}" in quote_padded for term in claim_terms)


def _verified_claim(item: Any, shown: dict[int, ArchiveMessage]) -> tuple[str, tuple[ClaimSupport, ...]] | None:
    """Keep a claim only with at least one support whose quote is verbatim from
    a shown message and whose content matches the claim."""
    if not isinstance(item, dict):
        return None
    text = _clean_text(item.get("text"))
    if not text:
        return None
    supports: list[ClaimSupport] = []
    for support in item.get("support") or ():
        if not isinstance(support, dict):
            continue
        try:
            message_id = int(support.get("id"))
        except (TypeError, ValueError):
            continue
        quote = _clean_text(support.get("quote"))
        message = shown.get(message_id)
        if message is None or not _quote_in_message(quote, message.text):
            continue
        if any(existing.message_id == message_id for existing in supports):
            continue
        supports.append(ClaimSupport(message_id=message_id, source_ref=str(message_id), quote=_clip(quote, 240)))
    if not supports or not _shares_content(text, [s.quote for s in supports]):
        return None
    return text, tuple(supports)


def _grounded_answer(payload: dict[str, Any], shown: dict[int, ArchiveMessage], *, calls: int) -> AnswerResult:
    if not payload.get("answer_found"):
        return _not_discussed(calls)
    host = _verified_claim(payload.get("javan_view"), shown)
    points = [p for p in (_verified_claim(v, shown) for v in payload.get("points") or ()) if p]
    disagreements = [p for p in (_verified_claim(v, shown) for v in payload.get("disagreements") or ()) if p]
    if not (points or host):
        return _not_discussed(calls)
    # The headline answer and the takeaway obey the same rule; an unsupported
    # headline is replaced by the strongest verified point, never shown as is.
    direct = _verified_claim(payload.get("direct_answer"), shown)
    conclusion = _verified_claim(payload.get("practical_conclusion"), shown)
    headline = direct[0] if direct else (host or points[0])[0]

    claims: list[GroundedClaim] = []
    if direct:
        claims.append(GroundedClaim(kind="answer", text=direct[0], supports=direct[1]))
    for kind, (text, supports) in ([("host", host)] if host else []) + [("finding", p) for p in points] + [("disagreement", p) for p in disagreements]:
        claims.append(GroundedClaim(kind=kind, text=text, supports=supports))
    cited_ids = tuple(dict.fromkeys(s.message_id for claim in claims for s in claim.supports))
    authors = {shown[i].author for i in cited_ids if shown[i].author}
    findings = ([f"نظر دکتر جوان: {host[0]}"] if host else []) + [text for text, _ in points]
    confidence = str(payload.get("confidence") or "medium")
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    if len(authors) < 2 and confidence == "high":
        confidence = "medium"
    years = sorted({shown[i].datetime.year for i in cited_ids if shown[i].datetime})
    span = f"، {years[0]}–{years[-1]}" if len(years) > 1 else (f"، {years[0]}" if years else "")
    return AnswerResult(
        direct_answer=headline,
        key_findings=tuple(findings),
        disagreements=tuple(text for text, _ in disagreements),
        practical_conclusion=conclusion[0] if conclusion else None,
        confidence=confidence,
        confidence_reason=f"{len(cited_ids)} پیام از {len(authors)} نفر{span}",
        cited_message_ids=cited_ids,
        source_refs=(),
        evidence_used_count=len(cited_ids),
        independent_authors_count=len(authors),
        insufficient_evidence=False,
        safety_note_if_needed=None,
        grounded_claims=tuple(claims),
        source_mode="archive",
        ai_calls=calls,
    )


def _not_discussed(calls: int) -> AnswerResult:
    return AnswerResult(
        direct_answer="این موضوع در پیام‌های گروه بحث نشده یا بحثی که مستقیماً به این سؤال جواب بده پیدا نشد.",
        key_findings=(), disagreements=(), practical_conclusion=None, confidence="low",
        confidence_reason="not_discussed", cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=True, safety_note_if_needed=None,
        source_mode="archive", limitations=("not_discussed",), ai_calls=calls,
    )


def _plain(text: str, *, ai_calls: int) -> AnswerResult:
    return AnswerResult(
        direct_answer=text, key_findings=(), disagreements=(), practical_conclusion=None, confidence="low",
        confidence_reason="conversation", cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=False, safety_note_if_needed=None,
        source_mode="archive", ai_calls=ai_calls,
    )


def _author_count(discussions: Sequence[Discussion]) -> int:
    return len({m.author for d in discussions for m in d.messages if m.author})


def _clip(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _emit(progress: ProgressCallback | None, stage: str, **details: object) -> None:
    if progress is None:
        return
    try:
        progress(stage, dict(details))
    except Exception:  # progress UI must never break answering
        _LOG.debug("progress_callback_failed stage=%s", stage)


__all__ = ["ArchiveBrain", "BRAIN_VERSION", "BrainResult", "JSONModel", "Turn", "Understanding"]
