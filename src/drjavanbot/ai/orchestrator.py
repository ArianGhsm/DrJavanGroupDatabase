from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
from typing import Callable, Protocol, Sequence

from drjavanbot.normalization import normalize_text
from drjavanbot.search import SearchBackend
from drjavanbot.secrets import AVALAI_API_KEY_SECRET, SecretStore
from .cache import ResponseCache
from .config import AIConfig
from .corpus import sqlite_corpus_hints
from .evidence import build_evidence_pack
from .models import AnswerResult, ProviderResult
from .planner import (
    PLANNER_VERSION,
    SearchPlan,
    deterministic_fallback_plan,
    observed_vocabulary,
    parse_refinement_families,
    parse_search_plan,
    plan_requires_deep_retrieval,
)
from .planner_cache import SearchPlanCache
from .prompts import (
    PROMPT_VERSION,
    REFINEMENT_SYSTEM_PROMPT,
    SEARCH_PLANNER_SYSTEM_PROMPT,
    SYNTHESIS_INSUFFICIENT_RECHECK_SUFFIX,
    SYNTHESIS_RETRY_SUFFIX,
    SYNTHESIS_SYSTEM_PROMPT,
    refinement_user_prompt,
    search_planner_user_prompt,
    synthesis_user_prompt,
)
from .provider import AvalAIClient
from .retrieval import assess_planned_retrieval, retrieve_with_plan
from .telemetry import TelemetryStore
from .validation import CitationValidationError, ModelOutputError, parse_json_object, validate_answer_payload

MAX_LOGICAL_AI_CALLS = 4
_MIN_PLANNER_OUTPUT_TOKENS = 480
_MIN_REFINEMENT_OUTPUT_TOKENS = 360
_MAX_RETRIEVAL_PREVIEW_MESSAGES = 14
_MAX_RETRIEVAL_PREVIEW_CHARS = 700
ProgressCallback = Callable[[str, dict[str, object]], None]


class AIConfigurationError(RuntimeError):
    pass


class AIProvider(Protocol):
    def chat_json(self, *, api_key: str, request_type: str, system_prompt: str, user_prompt: str, max_output_tokens: int) -> ProviderResult: ...


class ArchiveAnswerService:
    """AI planning -> faceted local retrieval -> bounded AI rescue -> grounded synthesis."""

    def __init__(
        self,
        *,
        backend: SearchBackend,
        secret_store: SecretStore,
        config: AIConfig,
        provider: AIProvider | None = None,
        cache: ResponseCache | None = None,
        planner_cache: SearchPlanCache | None = None,
        telemetry: TelemetryStore | None = None,
    ) -> None:
        self.backend = backend
        self.secret_store = secret_store
        self.config = config
        self.provider = provider or AvalAIClient(config)
        self.cache = cache
        self.planner_cache = planner_cache
        self.telemetry = telemetry

    def answer(self, question: str, progress: ProgressCallback | None = None) -> AnswerResult:
        """Answer from archive evidence while emitting only safe pipeline milestones."""
        question = question.strip()
        normalized = normalize_text(question)
        if not normalized:
            _emit_progress(progress, "no_evidence", reason="not_searchable")
            return _not_searchable_answer(ai_calls=0)

        index_version = _index_fingerprint(self.backend)
        cache_key = _cache_key(normalized, index_version, self.config)
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                _emit_progress(
                    progress,
                    "cache_hit",
                    evidence_used=int(cached.evidence_used_count),
                    authors=int(cached.independent_authors_count),
                )
                return cached.with_runtime(cache_hit=True, ai_calls=0)

        api_key = self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
        if not api_key:
            raise AIConfigurationError("AvalAI API key is not configured")

        ai_calls = 0
        refinement_used = False
        planner_key = _planner_cache_key(normalized, index_version, self.config)
        plan = self.planner_cache.get(planner_key, question=question) if self.planner_cache is not None else None
        if plan is None:
            _emit_progress(progress, "planning", cached=False)
            ai_calls += 1
            planner_cacheable = True
            try:
                _, parsed = self._call_processed(
                    request_type="search_plan",
                    api_key=api_key,
                    system_prompt=SEARCH_PLANNER_SYSTEM_PROMPT,
                    user_prompt=search_planner_user_prompt(question),
                    max_output_tokens=max(self.config.planner_max_output_tokens, _MIN_PLANNER_OUTPUT_TOKENS),
                    processor=lambda content: parse_search_plan(content, question=question),
                )
                plan = parsed
            except (ModelOutputError, CitationValidationError):
                plan = deterministic_fallback_plan(question)
                planner_cacheable = False
            assert isinstance(plan, SearchPlan)
            if self.planner_cache is not None and planner_cacheable:
                self.planner_cache.set(planner_key, plan)
        else:
            _emit_progress(progress, "planning", cached=True)

        if not plan.searchable:
            _emit_progress(progress, "no_evidence", reason="planner_not_searchable")
            answer = _not_searchable_answer(ai_calls=ai_calls)
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        _emit_progress(
            progress,
            "searching",
            query_count=len(plan.queries),
            family_count=len(plan.query_families),
        )
        report = retrieve_with_plan(self.backend, plan)
        _emit_retrieval_progress(progress, report, refined=False)
        needs_refinement, retrieval_reason = assess_planned_retrieval(report)

        # A strong topical hit is not equivalent to answering a requested facet.
        # Timing, comparison, recommendation, cause, method and quantity questions
        # receive one bounded rescue pass even when lexical scores look healthy.
        facet_sensitive = plan_requires_deep_retrieval(plan)
        if facet_sensitive:
            needs_refinement = True
            retrieval_reason = f"answer_facet_check:{retrieval_reason}"

        if needs_refinement and ai_calls < MAX_LOGICAL_AI_CALLS - 1:
            _emit_progress(
                progress,
                "refining",
                candidate_count=len(report.candidates),
                author_count=_candidate_author_count(report.candidates),
                conversation_bridges=int(getattr(report, "conversation_bridges", 0)),
            )
            observed = observed_vocabulary(report.candidates, question=question)
            corpus_hints = _corpus_hints(self.backend, plan, observed)
            diagnostics = _retrieval_diagnostics(report, retrieval_reason)
            preview = _retrieval_preview(question, report.candidates, self.config)
            ai_calls += 1
            try:
                _, families = self._call_processed(
                    request_type="search_refinement",
                    api_key=api_key,
                    system_prompt=REFINEMENT_SYSTEM_PROMPT,
                    user_prompt=refinement_user_prompt(
                        question,
                        plan,
                        observed,
                        corpus_hints,
                        retrieval_diagnostics=diagnostics,
                        retrieval_preview=preview,
                    ),
                    max_output_tokens=max(self.config.refinement_max_output_tokens, _MIN_REFINEMENT_OUTPUT_TOKENS),
                    processor=parse_refinement_families,
                )
            except (ModelOutputError, CitationValidationError):
                families = ()
            if families:
                refinement_used = True
                plan = plan.with_added_families(families)
                _emit_progress(
                    progress,
                    "searching",
                    query_count=len(plan.queries),
                    family_count=len(plan.query_families),
                    refined=True,
                )
                report = retrieve_with_plan(self.backend, plan)
                _emit_retrieval_progress(progress, report, refined=True)

        candidates = report.candidates
        if not candidates:
            _emit_progress(progress, "no_evidence", reason="no_candidates")
            answer = _insufficient_answer(ai_calls=ai_calls, refinement_used=refinement_used)
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        pack = build_evidence_pack(question, candidates, self.config)
        if not pack.messages:
            _emit_progress(progress, "no_evidence", reason="empty_evidence_pack")
            answer = _insufficient_answer(ai_calls=ai_calls, refinement_used=refinement_used)
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        if ai_calls >= MAX_LOGICAL_AI_CALLS:
            _emit_progress(progress, "validation_failed", ai_calls=ai_calls)
            return _structured_output_failure_answer(ai_calls=ai_calls, refinement_used=refinement_used)

        evidence_authors = len({m.author for m in pack.messages if m.author})
        _emit_progress(
            progress,
            "synthesizing",
            evidence_messages=len(pack.messages),
            evidence_authors=evidence_authors,
            estimated_tokens=pack.estimated_tokens,
        )
        budget = self.config.budget_for(question)
        synthesis_kwargs = dict(
            request_type="synthesis",
            api_key=api_key,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=synthesis_user_prompt(pack, plan=plan),
            max_output_tokens=budget.max_output_tokens,
            processor=lambda content: _validate_synthesis_content(content, pack, question),
            before_process=lambda: _emit_progress(
                progress,
                "validating",
                evidence_messages=len(pack.messages),
                evidence_authors=evidence_authors,
            ),
        )
        cacheable = True
        ai_calls += 1
        try:
            _, answer = self._call_processed(**synthesis_kwargs)
        except (ModelOutputError, CitationValidationError):
            if ai_calls >= MAX_LOGICAL_AI_CALLS:
                cacheable = False
                _emit_progress(progress, "validation_failed", ai_calls=ai_calls)
                answer = _structured_output_failure_answer(ai_calls=ai_calls, refinement_used=refinement_used)
            else:
                retry_tokens = max(
                    budget.max_output_tokens,
                    min(
                        self.config.structured_retry_output_tokens,
                        max(budget.max_output_tokens * 2, 1_200),
                    ),
                )
                _emit_progress(progress, "repairing", ai_calls=ai_calls + 1, reason="invalid_structure")
                retry_kwargs = dict(synthesis_kwargs)
                retry_kwargs["system_prompt"] = SYNTHESIS_SYSTEM_PROMPT + SYNTHESIS_RETRY_SUFFIX
                retry_kwargs["max_output_tokens"] = retry_tokens
                ai_calls += 1
                try:
                    _, answer = self._call_processed(**retry_kwargs)
                except (ModelOutputError, CitationValidationError):
                    cacheable = False
                    _emit_progress(progress, "validation_failed", ai_calls=ai_calls)
                    answer = _structured_output_failure_answer(ai_calls=ai_calls, refinement_used=refinement_used)

        # A valid `insufficient_evidence=true` is not automatically final for a
        # faceted question. The common false-negative is a Telegram thread where
        # the topic is in one message and the requested age/number/recommendation
        # is in a neighboring short reply. If one logical call remains, ask the
        # model to re-read the SAME admitted evidence once. Exact-quote validation
        # remains unchanged, so this cannot turn model memory into an answer.
        if (
            answer.insufficient_evidence
            and facet_sensitive
            and ai_calls < MAX_LOGICAL_AI_CALLS
            and bool(pack.messages)
        ):
            _emit_progress(progress, "repairing", ai_calls=ai_calls + 1, reason="insufficient_recheck")
            recheck_kwargs = dict(synthesis_kwargs)
            recheck_kwargs["request_type"] = "synthesis_recheck"
            recheck_kwargs["system_prompt"] = SYNTHESIS_SYSTEM_PROMPT + SYNTHESIS_INSUFFICIENT_RECHECK_SUFFIX
            recheck_kwargs["max_output_tokens"] = max(
                budget.max_output_tokens,
                min(self.config.structured_retry_output_tokens, max(budget.max_output_tokens * 2, 1_200)),
            )
            ai_calls += 1
            original_insufficient = answer
            try:
                _, reconsidered = self._call_processed(**recheck_kwargs)
            except (ModelOutputError, CitationValidationError):
                # The first answer was valid and safely insufficient. A malformed
                # recheck must not replace it with a generic internal failure.
                answer = original_insufficient
            else:
                answer = reconsidered

        answer = replace(
            answer,
            cache_hit=False,
            ai_calls=ai_calls,
            expansion_used=refinement_used,
            evidence_pack_estimated_tokens=pack.estimated_tokens,
        )
        if self.cache is not None and cacheable:
            self.cache.set(cache_key, answer)
        _emit_progress(
            progress,
            "done",
            evidence_used=int(answer.evidence_used_count),
            authors=int(answer.independent_authors_count),
            insufficient=bool(answer.insufficient_evidence),
        )
        return answer

    def _call_processed(
        self,
        *,
        request_type: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        processor: Callable[[str], object],
        before_process: Callable[[], None] | None = None,
    ) -> tuple[ProviderResult, object]:
        started = time.perf_counter()
        result: ProviderResult | None = None
        try:
            result = self.provider.chat_json(
                api_key=api_key,
                request_type=request_type,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=max_output_tokens,
            )
            if before_process is not None:
                before_process()
            processed = processor(result.content)
        except Exception as exc:
            if self.telemetry is not None:
                self.telemetry.record(
                    request_type=request_type,
                    model=(result.model if result is not None else self.config.model),
                    latency_ms=(result.latency_ms if result is not None else (time.perf_counter() - started) * 1000),
                    success=False,
                    usage=(result.usage if result is not None else None),
                    error_class=type(exc).__name__,
                )
            raise
        if self.telemetry is not None:
            self.telemetry.record(
                request_type=request_type,
                model=result.model or self.config.model,
                latency_ms=result.latency_ms,
                success=True,
                usage=result.usage,
            )
        return result, processed


def _retrieval_preview(question: str, candidates: Sequence, config: AIConfig) -> tuple[dict[str, object], ...]:
    """PII-redacted, bounded real archive text for the retrieval critic only."""
    if not candidates:
        return ()
    pack = build_evidence_pack(question, tuple(candidates)[:20], config)
    preview: list[dict[str, object]] = []
    for item in pack.messages[:_MAX_RETRIEVAL_PREVIEW_MESSAGES]:
        text = (item.text or "").strip()
        if len(text) > _MAX_RETRIEVAL_PREVIEW_CHARS:
            half = (_MAX_RETRIEVAL_PREVIEW_CHARS - 3) // 2
            text = text[:half].rstrip() + " … " + text[-half:].lstrip()
        preview.append({
            "message_id": item.message_id,
            "role": item.role,
            "text": text,
        })
    return tuple(preview)


def _emit_progress(progress: ProgressCallback | None, stage: str, **details: object) -> None:
    if progress is None:
        return
    try:
        progress(stage, dict(details))
    except Exception:
        return


def _emit_retrieval_progress(progress: ProgressCallback | None, report, *, refined: bool) -> None:
    _emit_progress(
        progress,
        "context",
        candidate_count=len(report.candidates),
        author_count=_candidate_author_count(report.candidates),
        context_hydrated=int(getattr(report, "context_hydrated", 0)),
        discussion_windows=int(getattr(report, "discussion_windows", 0)),
        conversation_bridges=int(getattr(report, "conversation_bridges", 0)),
        families_with_hits=int(getattr(report, "families_with_hits", 0)),
        refined=bool(refined),
    )


def _retrieval_diagnostics(report, reason: str) -> dict[str, object]:
    return {
        "assessment": reason,
        "candidate_count": len(report.candidates),
        "author_count": _candidate_author_count(report.candidates),
        "families_with_hits": int(getattr(report, "families_with_hits", 0)),
        "hit_family_names": list(getattr(report, "hit_family_names", ())[:10]),
        "context_hydrated": int(getattr(report, "context_hydrated", 0)),
        "discussion_windows": int(getattr(report, "discussion_windows", 0)),
        "conversation_bridges": int(getattr(report, "conversation_bridges", 0)),
    }


def _candidate_author_count(candidates) -> int:
    return len({
        candidate.message.author_normalized or candidate.message.author
        for candidate in tuple(candidates)[:24]
        if candidate.message.author_normalized or candidate.message.author
    })


def _validate_synthesis_content(content: str, pack, question: str) -> AnswerResult:
    return validate_answer_payload(parse_json_object(content), pack, question=question)


def _corpus_hints(backend: SearchBackend, plan: SearchPlan, observed: tuple[str, ...]) -> tuple[str, ...]:
    """Use vocabulary from the current local index; search hints are never evidence."""
    seeds = tuple(dict.fromkeys((*plan.core_concepts, *plan.aliases, *plan.optional_concepts, *observed[:16])))
    provider = getattr(backend, "corpus_hints", None)
    try:
        if callable(provider):
            values = provider(seeds, limit=32)
        else:
            values = sqlite_corpus_hints(backend, seeds, limit=32)
    except Exception:
        values = ()

    out: list[str] = []
    seen: set[str] = set()
    for value in (*tuple(values or ()), *observed):
        text = str(value).strip()
        key = normalize_text(text)
        if text and key and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= 32:
            break
    return tuple(out)


def _index_fingerprint(backend: SearchBackend) -> str:
    stats = dict(backend.stats())
    explicit = stats.get("index_version")
    parts: dict[str, object] = {"stats": stats}
    db_path = getattr(backend, "db_path", None)
    if db_path is not None:
        try:
            stat = Path(db_path).stat()
            parts["db"] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": getattr(stat, "st_ino", 0)}
        except OSError:
            pass
    if explicit is not None:
        parts["explicit_index_version"] = explicit
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_key(normalized_question: str, index_version: str, config: AIConfig) -> str:
    raw = "\n".join((normalized_question, index_version, PROMPT_VERSION, config.model, config.cache_signature())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _planner_cache_key(normalized_question: str, index_version: str, config: AIConfig) -> str:
    raw = "\n".join((normalized_question, index_version, PLANNER_VERSION, config.model)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _not_searchable_answer(*, ai_calls: int) -> AnswerResult:
    return AnswerResult(
        direct_answer="سؤال قابل جست‌وجوی معناداری برای آرشیو پیدا نشد. لطفاً موضوع مشخص‌تری بفرستید.",
        key_findings=(), disagreements=(), practical_conclusion=None,
        confidence="low", confidence_reason="Search planner موضوع معناداری برای بازیابی آرشیو پیدا نکرد.",
        cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=True,
        safety_note_if_needed=None, cache_hit=False, ai_calls=ai_calls,
        expansion_used=False, evidence_pack_estimated_tokens=0,
    )


def _structured_output_failure_answer(*, ai_calls: int, refinement_used: bool) -> AnswerResult:
    return AnswerResult(
        direct_answer="پاسخ ساختاری سرویس AI این بار معتبر نبود. لطفاً همان سؤال را دوباره بفرستید.",
        key_findings=(), disagreements=(), practical_conclusion=None,
        confidence="low", confidence_reason="خروجی مدل در سقف محدود فراخوانی‌ها قابل اعتبارسنجی نبود.",
        cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=True,
        safety_note_if_needed=None, cache_hit=False, ai_calls=ai_calls,
        expansion_used=refinement_used, evidence_pack_estimated_tokens=0,
    )


def _insufficient_answer(*, ai_calls: int, refinement_used: bool) -> AnswerResult:
    return AnswerResult(
        direct_answer="در آرشیو پیام‌های بازیابی‌شده شواهد کافی برای پاسخ قابل اتکا پیدا نشد.",
        key_findings=(), disagreements=(), practical_conclusion=None,
        confidence="low", confidence_reason="جست‌وجوی چندمرحله‌ای و context گفت‌وگو evidence کافی پیدا نکرد.",
        cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=True,
        safety_note_if_needed=None,
        cache_hit=False,
        ai_calls=ai_calls,
        expansion_used=refinement_used,
        evidence_pack_estimated_tokens=0,
    )
