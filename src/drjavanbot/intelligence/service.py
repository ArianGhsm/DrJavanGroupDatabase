from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import AnswerResult
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.secrets import AVALAI_API_KEY_SECRET, SecretStore
from .answerability import assess_requested_fact_coverage
from .archive_provider import ArchiveRetrievalProvider
from .cache import SourceAwareResponseCache, cache_key, ttl_for_route
from .current_provider import CurrentInformationProvider, OfficialInformationProvider
from .fusion import MultiSourceEvidenceFusion
from .model_policy import ModelPolicy
from .models import SourceType
from .orchestration import MultiSourceRetrievalOrchestrator
from .retrieval import RetrievalRegistry, UnavailableRetrievalProvider
from .scientific_provider import PubMedScientificProvider
from .synthesis import GroundedMultiSourceSynthesizer

ProgressCallback = Callable[[str, dict[str, object]], None]


class MultiSourceAnswerService:
    def __init__(
        self,
        *,
        backend: SQLiteSearchBackend,
        secret_store: SecretStore,
        config: AIConfig,
        cache: SourceAwareResponseCache | None = None,
        telemetry: TelemetryStore | None = None,
        model_policy: ModelPolicy | None = None,
        registry: RetrievalRegistry | None = None,
    ) -> None:
        self.backend = backend
        self.secret_store = secret_store
        self.config = config
        self.cache = cache
        self.telemetry = telemetry
        self.model_policy = model_policy or ModelPolicy.from_env(default_model=config.model)
        if registry is None:
            registry = RetrievalRegistry((
                ArchiveRetrievalProvider(backend),
                PubMedScientificProvider(),
                CurrentInformationProvider(),
                OfficialInformationProvider(),
                UnavailableRetrievalProvider(SourceType.DENTAL_KNOWLEDGE, "curated_kb_not_configured"),
            ))
        self.registry = registry
        self.retrieval = MultiSourceRetrievalOrchestrator(registry)
        self.fusion = MultiSourceEvidenceFusion()

    def answer(self, plan, *, progress: ProgressCallback | None = None, understanding_ai_calls: int = 0) -> AnswerResult:
        understanding = plan.understanding
        route = plan.route
        key = cache_key(
            understanding, route, model_signature=self._model_signature(),
            archive_fingerprint=_archive_fingerprint(self.backend),
        )
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                _emit(progress, "cache_hit", source_mode=cached.source_mode, evidence_used=cached.evidence_used_count)
                return cached.with_runtime(cache_hit=True, ai_calls=0)

        _emit(progress, "source_routing", sources=len(route.selected_sources), required=len(route.required_sources))
        required_types = {str(value) for value in route.required_sources}
        required_requests = tuple(req for req in plan.retrieval_requests if str(req.source_type) in required_types)
        optional_requests = tuple(req for req in plan.retrieval_requests if str(req.source_type) not in required_types)
        _emit(progress, "source_retrieval", sources=len(required_requests), optional_deferred=len(optional_requests))
        batch = self.retrieval.retrieve(required_requests)
        evidence = tuple(item for result in batch.results for item in result.items)
        ranked = self.fusion.rerank(understanding, route, evidence, top_k=20)
        coverage = assess_requested_fact_coverage(understanding, route, (value.item for value in ranked))
        # Optional sources are supplementary. They are queried only when required
        # evidence has not yet established answerability, so slow optional web or
        # archive paths cannot block a complete required-source answer.
        optional_unavailable: tuple[str, ...] = ()
        if not coverage.answerable and optional_requests:
            optional_batch = self.retrieval.retrieve(optional_requests)
            optional_unavailable = optional_batch.unavailable_sources
            evidence = evidence + tuple(item for result in optional_batch.results for item in result.items)
            ranked = self.fusion.rerank(understanding, route, evidence, top_k=20)
            coverage = assess_requested_fact_coverage(understanding, route, (value.item for value in ranked))
        _emit(progress, "evidence_fusion", evidence_count=len(evidence), unavailable_count=len(batch.unavailable_sources) + len(optional_unavailable))
        _emit(progress, "answerability", answerable=coverage.answerable, evidence_count=coverage.evidence_count,
              source_requirement_satisfied=coverage.source_requirement_satisfied, freshness_satisfied=coverage.freshness_satisfied)

        if not coverage.answerable:
            answer = _insufficient_answer(route, coverage.reason_code, understanding_ai_calls)
        else:
            api_key = self.secret_store.get_secret(AVALAI_API_KEY_SECRET)
            if not api_key:
                answer = _insufficient_answer(route, "ai_not_configured", understanding_ai_calls)
            else:
                _emit(progress, "synthesizing", evidence_messages=len(ranked), evidence_authors=coverage.independent_sources)
                synthesizer = GroundedMultiSourceSynthesizer(
                    api_key=api_key, base_config=self.config, model_policy=self.model_policy, telemetry=self.telemetry,
                )
                answer = synthesizer.synthesize(understanding, route, ranked, coverage)
                answer = answer.with_runtime(ai_calls=answer.ai_calls + understanding_ai_calls)
                _emit(progress, "validating", evidence_used=answer.evidence_used_count)
        if self.cache is not None:
            self.cache.set(key, answer, ttl_seconds=ttl_for_route(understanding, route))
        _emit(progress, "done", source_mode=answer.source_mode, evidence_used=answer.evidence_used_count)
        return answer

    def _model_signature(self) -> str:
        policy = self.model_policy
        return ":".join((policy.fast_model, policy.reasoning_model, str(policy.fast_output_tokens),
                         str(policy.reasoning_output_tokens), str(policy.synthesis_output_tokens), policy.strong_reasoning_effort))


def _archive_fingerprint(backend: SQLiteSearchBackend) -> str:
    try:
        stat = backend.db_path.stat()
        raw = f"{stat.st_size}:{stat.st_mtime_ns}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]
    except OSError:
        return "archive-unavailable"


def _source_mode(route) -> str:
    required = {str(value) for value in route.required_sources}
    if required == {SourceType.ARCHIVE}: return "archive"
    if SourceType.ARCHIVE in required and len(required) > 1: return "hybrid"
    if required & {SourceType.CURRENT_WEB, SourceType.OFFICIAL}: return "current"
    if SourceType.SCIENTIFIC in required: return "scientific"
    return "hybrid"


def _insufficient_answer(route, reason: str, ai_calls: int) -> AnswerResult:
    mode = _source_mode(route)
    label = {"archive":"آرشیو گروه", "scientific":"منابع علمی معتبر", "current":"منابع به‌روز", "hybrid":"همه منابع لازم"}.get(mode, "منابع لازم")
    return AnswerResult(
        direct_answer=f"در {label} شواهد کافی برای پاسخ مطمئن پیدا نشد؛ برای جلوگیری از حدس، پاسخ قطعی ساخته نشد.",
        key_findings=(), disagreements=(), practical_conclusion=None, confidence="low", confidence_reason=reason,
        cited_message_ids=(), source_refs=(), evidence_used_count=0, independent_authors_count=0,
        insufficient_evidence=True, safety_note_if_needed=None, source_mode=mode, limitations=(reason,), ai_calls=ai_calls,
    )


def _emit(progress: ProgressCallback | None, stage: str, **details: object) -> None:
    if progress is not None:
        try: progress(stage, dict(details))
        except Exception: pass


__all__ = ["MultiSourceAnswerService"]
