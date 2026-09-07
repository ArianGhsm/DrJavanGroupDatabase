from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
from typing import Callable, Protocol, Sequence

from drjavanbot.normalization import normalize_text
from drjavanbot.search import EvidenceCandidate, SearchBackend, SearchQuery
from drjavanbot.secrets import AVALAI_API_KEY_SECRET, SecretStore
from .cache import ResponseCache
from .config import AIConfig
from .evidence import assess_retrieval, build_evidence_pack
from .models import AnswerResult, ProviderResult
from .prompts import (
    PROMPT_VERSION,
    QUERY_EXPANSION_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_RETRY_SUFFIX,
    query_expansion_user_prompt,
    synthesis_user_prompt,
)
from .provider import AvalAIClient
from .telemetry import TelemetryStore
from .validation import CitationValidationError, ModelOutputError, parse_json_object, parse_query_variants, validate_answer_payload


class AIConfigurationError(RuntimeError):
    pass


class AIProvider(Protocol):
    def chat_json(self, *, api_key: str, request_type: str, system_prompt: str, user_prompt: str, max_output_tokens: int) -> ProviderResult: ...


class ArchiveAnswerService:
    """Question -> local retrieval -> optional expansion -> bounded evidence -> synthesis."""

    def __init__(
        self,
        *,
        backend: SearchBackend,
        secret_store: SecretStore,
        config: AIConfig,
        provider: AIProvider | None = None,
        cache: ResponseCache | None = None,
        telemetry: TelemetryStore | None = None,
    ) -> None:
        self.backend = backend
        self.secret_store = secret_store
        self.config = config
        self.provider = provider or AvalAIClient(config)
        self.cache = cache
        self.telemetry = telemetry

    def answer(self, question: str) -> AnswerResult:
        question = question.strip()
        normalized = normalize_text(question)
        if not normalized:
            return _not_searchable_answer()

        index_version = _index_fingerprint(self.backend)
        cache_key = _cache_key(normalized, index_version, self.config)
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached.with_runtime(cache_hit=True, ai_calls=0)

        candidates = tuple(self.backend.search(SearchQuery(raw_query=question)))
        needs_expansion, _ = assess_retrieval(candidates)
        api_key = self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
        ai_calls = 0
        expansion_used = False

        if needs_expansion:
            if not api_key:
                raise AIConfigurationError("AvalAI API key is not configured")
            observed = _observed_terms(candidates)
            try:
                _, variants = self._call_processed(
                    request_type="expansion",
                    api_key=api_key,
                    system_prompt=QUERY_EXPANSION_SYSTEM_PROMPT,
                    user_prompt=query_expansion_user_prompt(question, observed),
                    max_output_tokens=self.config.expansion_max_output_tokens,
                    processor=lambda content: parse_query_variants(content, original=question),
                )
            except (ModelOutputError, CitationValidationError):
                variants = ()
            ai_calls += 1
            if variants:
                expansion_used = True
                candidates = tuple(self.backend.search(SearchQuery(raw_query=question, variants=variants)))

        if not candidates:
            answer = _insufficient_answer(ai_calls=ai_calls, expansion_used=expansion_used)
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        pack = build_evidence_pack(question, candidates, self.config)
        if not pack.messages:
            answer = _insufficient_answer(ai_calls=ai_calls, expansion_used=expansion_used)
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        if not api_key:
            api_key = self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
        if not api_key:
            raise AIConfigurationError("AvalAI API key is not configured")

        budget = self.config.budget_for(question)
        synthesis_kwargs = dict(
            request_type="synthesis",
            api_key=api_key,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=synthesis_user_prompt(pack),
            max_output_tokens=budget.max_output_tokens,
            processor=lambda content: _validate_synthesis_content(content, pack, question),
        )
        cacheable = True
        try:
            _, answer = self._call_processed(**synthesis_kwargs)
            ai_calls += 1
        except (ModelOutputError, CitationValidationError):
            # A second call is allowed only for malformed/incomplete structured output.
            # Unlike the old behavior, retrying does not repeat the same too-small cap.
            ai_calls += 1
            retry_tokens = max(
                budget.max_output_tokens,
                min(
                    self.config.structured_retry_output_tokens,
                    max(budget.max_output_tokens * 2, 1_200),
                ),
            )
            retry_kwargs = dict(synthesis_kwargs)
            retry_kwargs["system_prompt"] = SYNTHESIS_SYSTEM_PROMPT + SYNTHESIS_RETRY_SUFFIX
            retry_kwargs["max_output_tokens"] = retry_tokens
            try:
                _, answer = self._call_processed(**retry_kwargs)
                ai_calls += 1
            except (ModelOutputError, CitationValidationError):
                ai_calls += 1
                cacheable = False
                answer = _structured_output_failure_answer(ai_calls=ai_calls, expansion_used=expansion_used)

        answer = replace(
            answer,
            cache_hit=False,
            ai_calls=ai_calls,
            expansion_used=expansion_used,
            evidence_pack_estimated_tokens=pack.estimated_tokens,
        )
        if self.cache is not None and cacheable:
            self.cache.set(cache_key, answer)
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


def _validate_synthesis_content(content: str, pack, question: str) -> AnswerResult:
    return validate_answer_payload(parse_json_object(content), pack, question=question)


def _observed_terms(candidates: Sequence[EvidenceCandidate]) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates[:10]:
        for term in candidate.matched_terms:
            key = term.casefold()
            if term and key not in seen:
                seen.add(key)
                terms.append(term)
    return tuple(terms[:20])


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


def _not_searchable_answer() -> AnswerResult:
    return AnswerResult(
        direct_answer="سؤال قابل جست‌وجویی در پیام شما پیدا نشد. لطفاً سؤال را با چند واژه مشخص بفرستید.",
        key_findings=(), disagreements=(), practical_conclusion=None,
        confidence="low", confidence_reason="عبارت جست‌وجوی معناداری وجود ندارد.",
        cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=True,
        safety_note_if_needed=None, cache_hit=False, ai_calls=0,
        expansion_used=False, evidence_pack_estimated_tokens=0,
    )


def _structured_output_failure_answer(*, ai_calls: int, expansion_used: bool) -> AnswerResult:
    return AnswerResult(
        direct_answer="پاسخ ساختاری سرویس AI این بار معتبر نبود. لطفاً همان سؤال را دوباره بفرستید.",
        key_findings=(), disagreements=(), practical_conclusion=None,
        confidence="low", confidence_reason="خروجی مدل پس از یک retry محدود قابل اعتبارسنجی نبود.",
        cited_message_ids=(), source_refs=(), evidence_used_count=0,
        independent_authors_count=0, insufficient_evidence=True,
        safety_note_if_needed=None, cache_hit=False, ai_calls=ai_calls,
        expansion_used=expansion_used, evidence_pack_estimated_tokens=0,
    )


def _insufficient_answer(*, ai_calls: int, expansion_used: bool) -> AnswerResult:
    return AnswerResult(
        direct_answer="در آرشیو پیام‌های بازیابی‌شده شواهد کافی برای پاسخ قابل اتکا پیدا نشد.",
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason="بازیابی محلی پس از جست‌وجوی موجود، evidence کافی پیدا نکرد.",
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=None,
        cache_hit=False,
        ai_calls=ai_calls,
        expansion_used=expansion_used,
        evidence_pack_estimated_tokens=0,
    )
