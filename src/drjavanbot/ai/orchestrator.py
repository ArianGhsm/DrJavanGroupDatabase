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
from .integration_policy import (
    INTEGRATION_POLICY_VERSION,
    assess_integrated_answerability,
    bounded_refinement_families,
    should_refine_retrieval,
)
from .models import AnswerResult, ProviderResult
from .planner import (
    PLANNER_VERSION,
    SearchPlan,
    deterministic_fallback_plan,
    observed_vocabulary,
    parse_refinement_families,
    parse_search_plan,
)
from .planner_cache import SearchPlanCache
from .prompts import (
    PROMPT_VERSION,
    REFINEMENT_SYSTEM_PROMPT,
    SEARCH_PLANNER_SYSTEM_PROMPT,
    refinement_user_prompt,
    search_planner_user_prompt,
)
from .provider import AvalAIClient, ProviderError
from .reasoning import (
    VALIDATION_SEMANTICS_VERSION,
    ClaimExtraction,
    compose_verified_answer,
    parse_claim_extraction,
    parse_verifier_output,
    select_verified_claims,
    semantic_candidates,
)
from .reasoning_prompts import (
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    CLAIM_REPAIR_SUFFIX,
    CLAIM_RESCUE_SUFFIX,
    CLAIM_VERIFIER_RETRY_SUFFIX,
    CLAIM_VERIFIER_SYSTEM_PROMPT,
    REASONING_PROMPT_VERSION,
    claim_extraction_user_prompt,
    claim_verifier_user_prompt,
)
from .retrieval import RETRIEVAL_SEMANTICS_VERSION, assess_planned_retrieval, retrieve_with_plan
from .telemetry import TelemetryStore
from .validation import CitationValidationError, ModelOutputError, OutputTruncatedError


# One hard application-level semantic-call ceiling. Provider-internal transport
# retries remain separately bounded by AIConfig and are not semantic loops.
MAX_LOGICAL_AI_CALLS = 4
_MIN_PLANNER_OUTPUT_TOKENS = 480
_MIN_REFINEMENT_OUTPUT_TOKENS = 360
_MAX_RETRIEVAL_PREVIEW_MESSAGES = 14
_MAX_RETRIEVAL_PREVIEW_CHARS = 700
ProgressCallback = Callable[[str, dict[str, object]], None]


class AIConfigurationError(RuntimeError):
    pass


class AIProvider(Protocol):
    def chat_json(
        self,
        *,
        api_key: str,
        request_type: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
    ) -> ProviderResult: ...


class ArchiveAnswerService:
    """Typed planning -> discussion retrieval -> verified archive reasoning."""

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

    def answer(self, question: str, progress: ProgressCallback | None = None, *, precomputed_plan: SearchPlan | None = None) -> AnswerResult:
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
        plan = precomputed_plan
        if plan is None and self.planner_cache is not None:
            plan = self.planner_cache.get(planner_key, question=question)

        # STATE: PLAN. Intelligence-v2 may provide a validated pre-search plan.
        if plan is None:
            _emit_progress(progress, "planning", cached=False)
            ai_calls += 1
            planner_cacheable = True
            try:
                _, parsed = self._call_processed(
                    request_type="search_plan",
                    stage="planning",
                    logical_call=ai_calls,
                    api_key=api_key,
                    system_prompt=SEARCH_PLANNER_SYSTEM_PROMPT,
                    user_prompt=search_planner_user_prompt(question),
                    max_output_tokens=max(self.config.planner_max_output_tokens, _MIN_PLANNER_OUTPUT_TOKENS),
                    processor=lambda content: parse_search_plan(content, question=question),
                )
                plan = parsed
            except ProviderError:
                return _provider_failure_answer(ai_calls=ai_calls, refinement_used=False)
            except (ModelOutputError, CitationValidationError):
                # Deterministic fallback understands generic question facets but
                # contains no clinical answer facts. A malformed model plan is not
                # cached, so the next request can try planning again.
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

        # STATE: RETRIEVE. Planner owns search breadth/depth policy; retrieval owns
        # whether that work actually found a coherent topic/facet discussion.
        _emit_progress(
            progress,
            "searching",
            query_count=len(plan.queries),
            family_count=len(plan.query_families),
            depth=str(getattr(plan.retrieval_policy, "depth", "standard")),
        )
        report = retrieve_with_plan(self.backend, plan)
        _emit_retrieval_progress(progress, report, refined=False)
        legacy_needs, legacy_reason = assess_planned_retrieval(report)
        needs_refinement, retrieval_reason = should_refine_retrieval(
            plan,
            report,
            legacy_needs_refinement=legacy_needs,
            legacy_reason=legacy_reason,
        )

        # STATE: optional bounded RETRIEVAL REFINE. A facet-complete discussion or
        # strong direct lookup stops here even when the original question is deep.
        if needs_refinement and ai_calls < MAX_LOGICAL_AI_CALLS - 1:
            _emit_progress(
                progress,
                "refining",
                candidate_count=len(report.candidates),
                author_count=_candidate_author_count(report.candidates),
                conversation_bridges=int(getattr(report, "conversation_bridges", 0)),
                quality_state=str(getattr(report, "quality_state", "")),
                reason=retrieval_reason,
            )
            observed = observed_vocabulary(report.candidates, question=question)
            corpus_hints = _corpus_hints(self.backend, plan, observed)
            diagnostics = _retrieval_diagnostics(report, retrieval_reason)
            preview = _retrieval_preview(question, report.candidates, self.config)
            ai_calls += 1
            try:
                _, families = self._call_processed(
                    request_type="search_refinement",
                    stage="refinement",
                    logical_call=ai_calls,
                    evidence_count=len(report.candidates),
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
                    processor=lambda content: parse_refinement_families(content, question=question),
                )
            except ProviderError:
                return _provider_failure_answer(ai_calls=ai_calls, refinement_used=refinement_used)
            except (ModelOutputError, CitationValidationError):
                families = ()

            families = bounded_refinement_families(plan, families)
            if families:
                refinement_used = True
                plan = plan.with_added_families(families)
                _emit_progress(
                    progress,
                    "searching",
                    query_count=len(plan.queries),
                    family_count=len(plan.query_families),
                    depth=str(getattr(plan.retrieval_policy, "depth", "standard")),
                    refined=True,
                )
                report = retrieve_with_plan(self.backend, plan)
                _emit_retrieval_progress(progress, report, refined=True)

        candidates = report.candidates
        if not candidates:
            _emit_progress(progress, "no_evidence", reason="no_candidates")
            answer = _insufficient_answer(
                ai_calls=ai_calls,
                refinement_used=refinement_used,
                reason_code="no_candidates",
            )
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        # STATE: PACK + ANSWERABILITY TRIAGE. Discussion-level co-location from
        # retrieval v2 is canonical; legacy lexical signals are fallback only.
        pack = build_evidence_pack(question, candidates, self.config)
        if not pack.messages:
            _emit_progress(progress, "no_evidence", reason="no_candidates")
            answer = _insufficient_answer(
                ai_calls=ai_calls,
                refinement_used=refinement_used,
                reason_code="no_candidates",
            )
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        answerability = assess_integrated_answerability(pack, plan, report)
        _emit_progress(progress, "answerability", **answerability.to_public_dict())

        if ai_calls >= MAX_LOGICAL_AI_CALLS:
            _emit_progress(progress, "validation_failed", ai_calls=ai_calls, reason="budget_exhausted")
            return _structured_output_failure_answer(
                ai_calls=ai_calls,
                refinement_used=refinement_used,
                reason_code="budget_exhausted",
            )

        budget = self.config.budget_for(question)
        evidence_authors = len({m.author for m in pack.messages if m.author})
        extraction_prompt = claim_extraction_user_prompt(pack, plan=plan, answerability=answerability)
        extraction_kwargs = dict(
            request_type="synthesis",  # external compatibility name; semantics are atomic extraction
            stage="extraction",
            api_key=api_key,
            system_prompt=CLAIM_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=extraction_prompt,
            max_output_tokens=budget.max_output_tokens,
            processor=lambda content: parse_claim_extraction(content, pack),
            evidence_count=len(pack.messages),
        )

        # STATE: EXTRACT
        _emit_progress(
            progress,
            "synthesizing",
            evidence_messages=len(pack.messages),
            evidence_authors=evidence_authors,
            estimated_tokens=pack.estimated_tokens,
        )
        ai_calls += 1
        try:
            _, extraction = self._call_processed(logical_call=ai_calls, **extraction_kwargs)
        except ProviderError:
            return _provider_failure_answer(
                ai_calls=ai_calls,
                refinement_used=refinement_used,
                evidence_tokens=pack.estimated_tokens,
            )
        except (ModelOutputError, CitationValidationError):
            extraction = None

        # One bounded structured/citation repair transition.
        if extraction is None:
            if ai_calls >= MAX_LOGICAL_AI_CALLS:
                _emit_progress(progress, "validation_failed", ai_calls=ai_calls, reason="structured_output_failed")
                return _structured_output_failure_answer(
                    ai_calls=ai_calls,
                    refinement_used=refinement_used,
                    reason_code="structured_output_failed",
                    evidence_tokens=pack.estimated_tokens,
                )
            retry_tokens = _repair_tokens(budget.max_output_tokens, self.config.structured_retry_output_tokens)
            _emit_progress(progress, "repairing", ai_calls=ai_calls + 1, reason="structured_output_failed")
            ai_calls += 1
            try:
                _, extraction = self._call_processed(
                    logical_call=ai_calls,
                    **{
                        **extraction_kwargs,
                        "stage": "extraction_repair",
                        "system_prompt": CLAIM_EXTRACTION_SYSTEM_PROMPT + CLAIM_REPAIR_SUFFIX,
                        "max_output_tokens": retry_tokens,
                    },
                )
            except ProviderError:
                return _provider_failure_answer(
                    ai_calls=ai_calls,
                    refinement_used=refinement_used,
                    evidence_tokens=pack.estimated_tokens,
                )
            except (ModelOutputError, CitationValidationError):
                _emit_progress(progress, "validation_failed", ai_calls=ai_calls, reason="structured_output_failed")
                return _structured_output_failure_answer(
                    ai_calls=ai_calls,
                    refinement_used=refinement_used,
                    reason_code="structured_output_failed",
                    evidence_tokens=pack.estimated_tokens,
                )

        assert isinstance(extraction, ClaimExtraction)

        # A model-level insufficient flag is not authoritative. Rescue exactly
        # once only when archive/discussion signals say this same pack is plausibly
        # answerable and budget remains.
        if extraction.insufficient_evidence:
            if answerability.answerable and ai_calls < MAX_LOGICAL_AI_CALLS:
                _emit_progress(progress, "repairing", ai_calls=ai_calls + 1, reason="fragmented_but_answerable")
                ai_calls += 1
                try:
                    _, rescued = self._call_processed(
                        logical_call=ai_calls,
                        **{
                            **extraction_kwargs,
                            "request_type": "synthesis_recheck",
                            "stage": "answerability_rescue",
                            "system_prompt": CLAIM_EXTRACTION_SYSTEM_PROMPT + CLAIM_RESCUE_SUFFIX,
                            "max_output_tokens": _repair_tokens(
                                budget.max_output_tokens,
                                self.config.structured_retry_output_tokens,
                            ),
                        },
                    )
                except ProviderError:
                    return _provider_failure_answer(
                        ai_calls=ai_calls,
                        refinement_used=refinement_used,
                        evidence_tokens=pack.estimated_tokens,
                    )
                except (ModelOutputError, CitationValidationError):
                    rescued = None
                if isinstance(rescued, ClaimExtraction) and not rescued.insufficient_evidence:
                    extraction = rescued
                else:
                    answer = _insufficient_answer(
                        ai_calls=ai_calls,
                        refinement_used=refinement_used,
                        reason_code="true_insufficient",
                        evidence_tokens=pack.estimated_tokens,
                    )
                    if self.cache is not None:
                        self.cache.set(cache_key, answer)
                    return answer
            else:
                reason = (
                    answerability.reason_code
                    if answerability.reason_code in {"topic_found_facet_missing", "conflicting_only", "no_candidates"}
                    else "true_insufficient"
                )
                answer = _insufficient_answer(
                    ai_calls=ai_calls,
                    refinement_used=refinement_used,
                    reason_code=reason,
                    evidence_tokens=pack.estimated_tokens,
                )
                if self.cache is not None:
                    self.cache.set(cache_key, answer)
                return answer

        # STATE: deterministic support validation already happened during parsing;
        # semantically paraphrased claims receive a conditional entailment verifier.
        semantic = semantic_candidates(extraction.claims)
        verdicts = ()
        if semantic:
            if ai_calls >= MAX_LOGICAL_AI_CALLS:
                verdicts = ()
            else:
                verifier_prompt = claim_verifier_user_prompt(semantic)
                expected_indices = tuple(index for index, _ in semantic)
                _emit_progress(
                    progress,
                    "validating",
                    evidence_messages=len(pack.messages),
                    evidence_authors=evidence_authors,
                    semantic_claims=len(semantic),
                )
                ai_calls += 1
                try:
                    _, verdicts = self._call_processed(
                        request_type="claim_verification",
                        stage="verification",
                        logical_call=ai_calls,
                        evidence_count=len(pack.messages),
                        api_key=api_key,
                        system_prompt=CLAIM_VERIFIER_SYSTEM_PROMPT,
                        user_prompt=verifier_prompt,
                        max_output_tokens=min(max(320, self.config.refinement_max_output_tokens), 800),
                        processor=lambda content: parse_verifier_output(content, expected_indices),
                    )
                except ProviderError:
                    return _provider_failure_answer(
                        ai_calls=ai_calls,
                        refinement_used=refinement_used,
                        evidence_tokens=pack.estimated_tokens,
                    )
                except (ModelOutputError, CitationValidationError):
                    verdicts = None

                if verdicts is None and ai_calls < MAX_LOGICAL_AI_CALLS:
                    _emit_progress(progress, "repairing", ai_calls=ai_calls + 1, reason="structured_output_failed")
                    ai_calls += 1
                    try:
                        _, verdicts = self._call_processed(
                            request_type="claim_verification_retry",
                            stage="verification_repair",
                            logical_call=ai_calls,
                            evidence_count=len(pack.messages),
                            api_key=api_key,
                            system_prompt=CLAIM_VERIFIER_SYSTEM_PROMPT + CLAIM_VERIFIER_RETRY_SUFFIX,
                            user_prompt=verifier_prompt,
                            max_output_tokens=min(max(480, self.config.refinement_max_output_tokens), 900),
                            processor=lambda content: parse_verifier_output(content, expected_indices),
                        )
                    except ProviderError:
                        return _provider_failure_answer(
                            ai_calls=ai_calls,
                            refinement_used=refinement_used,
                            evidence_tokens=pack.estimated_tokens,
                        )
                    except (ModelOutputError, CitationValidationError):
                        verdicts = None

                if verdicts is None:
                    _emit_progress(progress, "validation_failed", ai_calls=ai_calls, reason="structured_output_failed")
                    return _structured_output_failure_answer(
                        ai_calls=ai_calls,
                        refinement_used=refinement_used,
                        reason_code="structured_output_failed",
                        evidence_tokens=pack.estimated_tokens,
                    )

        verified = select_verified_claims(extraction.claims, verdicts or ())
        if not verified:
            if answerability.answerable:
                _emit_progress(progress, "validation_failed", ai_calls=ai_calls, reason="validation_failed")
                return _structured_output_failure_answer(
                    ai_calls=ai_calls,
                    refinement_used=refinement_used,
                    reason_code="validation_failed",
                    evidence_tokens=pack.estimated_tokens,
                )
            answer = _insufficient_answer(
                ai_calls=ai_calls,
                refinement_used=refinement_used,
                reason_code="true_insufficient",
                evidence_tokens=pack.estimated_tokens,
            )
            if self.cache is not None:
                self.cache.set(cache_key, answer)
            return answer

        # STATE: COMPOSE. No model call: prose is assembled solely from claims
        # already bound to exact archive support and, where needed, entailment.
        answer = compose_verified_answer(verified, pack, question=question)
        answer = replace(
            answer,
            cache_hit=False,
            ai_calls=ai_calls,
            expansion_used=refinement_used,
            evidence_pack_estimated_tokens=pack.estimated_tokens,
        )
        if self.cache is not None:
            self.cache.set(cache_key, answer)
        _emit_progress(
            progress,
            "done",
            evidence_used=int(answer.evidence_used_count),
            authors=int(answer.independent_authors_count),
            insufficient=False,
        )
        return answer

    def _call_processed(
        self,
        *,
        request_type: str,
        stage: str,
        logical_call: int,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        processor: Callable[[str], object],
        evidence_count: int = 0,
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
            if not isinstance(result.content, str) or not result.content.strip():
                raise ModelOutputError("empty model content")
            if str(result.finish_reason or "").casefold() in {"length", "max_tokens", "token_limit"}:
                raise OutputTruncatedError("provider output was truncated")
            processed = processor(result.content)
        except Exception as exc:
            if self.telemetry is not None:
                result_class, reason_code = _failure_classification(exc)
                self.telemetry.record(
                    request_type=request_type,
                    stage=stage,
                    result_class=result_class,
                    reason_code=reason_code,
                    logical_call=logical_call,
                    evidence_count=evidence_count,
                    finish_reason=(result.finish_reason if result is not None else None),
                    model=(result.model if result is not None else self.config.model),
                    latency_ms=(
                        result.latency_ms
                        if result is not None
                        else (time.perf_counter() - started) * 1000
                    ),
                    success=False,
                    usage=(result.usage if result is not None else None),
                    error_class=type(exc).__name__,
                )
            raise
        if self.telemetry is not None:
            self.telemetry.record(
                request_type=request_type,
                stage=stage,
                result_class="success",
                reason_code=None,
                logical_call=logical_call,
                evidence_count=evidence_count,
                finish_reason=result.finish_reason,
                model=result.model or self.config.model,
                latency_ms=result.latency_ms,
                success=True,
                usage=result.usage,
            )
        return result, processed


def _retrieval_preview(question: str, candidates: Sequence, config: AIConfig) -> tuple[dict[str, object], ...]:
    """PII-redacted bounded real archive text for the retrieval critic only."""
    if not candidates:
        return ()
    pack = build_evidence_pack(question, tuple(candidates)[:20], config)
    preview: list[dict[str, object]] = []
    for item in pack.messages[:_MAX_RETRIEVAL_PREVIEW_MESSAGES]:
        text = (item.text or "").strip()
        if len(text) > _MAX_RETRIEVAL_PREVIEW_CHARS:
            half = (_MAX_RETRIEVAL_PREVIEW_CHARS - 3) // 2
            text = text[:half].rstrip() + " … " + text[-half:].lstrip()
        preview.append({"message_id": item.message_id, "role": item.role, "text": text})
    return tuple(preview)


def _emit_progress(progress: ProgressCallback | None, stage: str, **details: object) -> None:
    if progress is None:
        return
    try:
        progress(stage, dict(details))
    except Exception:
        # Presentation/telemetry progress must never break answer generation.
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
        discussion_count=int(getattr(report, "discussion_count", 0)),
        facet_complete_discussions=int(getattr(report, "facet_complete_discussions", 0)),
        topic_anchored_discussions=int(getattr(report, "topic_anchored_discussions", 0)),
        hydrated_discussions=int(getattr(report, "hydrated_discussions", 0)),
        required_facet_groups_total=int(getattr(report, "required_facet_groups_total", 0)),
        max_required_facet_groups_hit=int(getattr(report, "max_required_facet_groups_hit", 0)),
        quality_state=str(getattr(report, "quality_state", "")),
        refined=bool(refined),
    )


def _retrieval_diagnostics(report, reason: str) -> dict[str, object]:
    return {
        "assessment": reason,
        "quality_state": str(getattr(report, "quality_state", "")),
        "candidate_count": len(report.candidates),
        "author_count": _candidate_author_count(report.candidates),
        "families_with_hits": int(getattr(report, "families_with_hits", 0)),
        "families_executed": int(getattr(report, "families_executed", 0)),
        "hit_family_names": list(getattr(report, "hit_family_names", ())[:10]),
        "context_hydrated": int(getattr(report, "context_hydrated", 0)),
        "discussion_count": int(getattr(report, "discussion_count", 0)),
        "hydrated_discussions": int(getattr(report, "hydrated_discussions", 0)),
        "facet_complete_discussions": int(getattr(report, "facet_complete_discussions", 0)),
        "topic_anchored_discussions": int(getattr(report, "topic_anchored_discussions", 0)),
        "topic_anchored_bridges": int(getattr(report, "topic_anchored_bridges", 0)),
        "required_facet_groups_total": int(getattr(report, "required_facet_groups_total", 0)),
        "max_required_facet_groups_hit": int(getattr(report, "max_required_facet_groups_hit", 0)),
        "duplicates_suppressed": int(getattr(report, "duplicates_suppressed", 0)),
    }


def _candidate_author_count(candidates) -> int:
    return len({
        candidate.message.author_normalized or candidate.message.author
        for candidate in tuple(candidates)[:24]
        if candidate.message.author_normalized or candidate.message.author
    })


def _corpus_hints(backend: SearchBackend, plan: SearchPlan, observed: tuple[str, ...]) -> tuple[str, ...]:
    """Use vocabulary from current local index; search hints are never evidence."""
    seeds = tuple(dict.fromkeys((
        *tuple(getattr(plan, "topic_anchors", ()) or ()),
        *plan.core_concepts,
        *plan.aliases,
        *plan.optional_concepts,
        *observed[:16],
    )))
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
            parts["db"] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "inode": getattr(stat, "st_ino", 0),
            }
        except OSError:
            pass
    if explicit is not None:
        parts["explicit_index_version"] = explicit
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_key(normalized_question: str, index_version: str, config: AIConfig) -> str:
    # Final-answer cache is invalidated by every semantic layer, not just DB bytes.
    raw = "\n".join((
        normalized_question,
        index_version,
        PLANNER_VERSION,
        RETRIEVAL_SEMANTICS_VERSION,
        INTEGRATION_POLICY_VERSION,
        PROMPT_VERSION,
        REASONING_PROMPT_VERSION,
        VALIDATION_SEMANTICS_VERSION,
        config.model,
        config.cache_signature(),
    )).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _planner_cache_key(normalized_question: str, index_version: str, config: AIConfig) -> str:
    raw = "\n".join((normalized_question, index_version, PLANNER_VERSION, config.model)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _repair_tokens(base: int, configured: int) -> int:
    return max(base, min(configured, max(base * 2, 1_200)))


def _failure_classification(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ProviderError):
        return "provider_failure", "provider_failed"
    if isinstance(exc, OutputTruncatedError):
        return "structured_output_failure", "output_truncated"
    if isinstance(exc, ModelOutputError):
        return "structured_output_failure", "structured_output_failed"
    if isinstance(exc, CitationValidationError):
        return "validation_failure", "validation_failed"
    return "internal_failure", "validation_failed"


def _not_searchable_answer(*, ai_calls: int) -> AnswerResult:
    return AnswerResult(
        direct_answer="سؤال قابل جست‌وجوی معناداری برای آرشیو پیدا نشد. لطفاً موضوع مشخص‌تری بفرستید.",
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason="Search planner موضوع معناداری برای بازیابی آرشیو پیدا نکرد.",
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=None,
        cache_hit=False,
        ai_calls=ai_calls,
        expansion_used=False,
        evidence_pack_estimated_tokens=0,
    )


def _provider_failure_answer(*, ai_calls: int, refinement_used: bool, evidence_tokens: int = 0) -> AnswerResult:
    return AnswerResult(
        direct_answer="سرویس AI این بار پاسخ قابل پردازش نداد. این وضعیت به معنی نبود شواهد در آرشیو نیست؛ لطفاً سؤال را دوباره بفرستید.",
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason="provider_failed؛ وضعیت آرشیو از خطای سرویس نتیجه‌گیری نشده است.",
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=None,
        cache_hit=False,
        ai_calls=ai_calls,
        expansion_used=refinement_used,
        evidence_pack_estimated_tokens=evidence_tokens,
    )


def _structured_output_failure_answer(
    *,
    ai_calls: int,
    refinement_used: bool,
    reason_code: str = "structured_output_failed",
    evidence_tokens: int = 0,
) -> AnswerResult:
    return AnswerResult(
        direct_answer="پاسخ ساختاری سرویس AI این بار قابل اعتبارسنجی نبود. لطفاً همان سؤال را دوباره بفرستید.",
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason=f"{reason_code}؛ هیچ claim تأییدنشده‌ای نمایش داده نشد.",
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=None,
        cache_hit=False,
        ai_calls=ai_calls,
        expansion_used=refinement_used,
        evidence_pack_estimated_tokens=evidence_tokens,
    )


def _insufficient_answer(
    *,
    ai_calls: int,
    refinement_used: bool,
    reason_code: str = "true_insufficient",
    evidence_tokens: int = 0,
) -> AnswerResult:
    return AnswerResult(
        direct_answer="در آرشیو پیام‌های بازیابی‌شده شواهد کافی برای پاسخ قابل اتکا پیدا نشد.",
        key_findings=(),
        disagreements=(),
        practical_conclusion=None,
        confidence="low",
        confidence_reason=f"{reason_code}؛ پاسخ factual فقط با evidence پذیرفته‌شده مجاز است.",
        cited_message_ids=(),
        source_refs=(),
        evidence_used_count=0,
        independent_authors_count=0,
        insufficient_evidence=True,
        safety_note_if_needed=None,
        cache_hit=False,
        ai_calls=ai_calls,
        expansion_used=refinement_used,
        evidence_pack_estimated_tokens=evidence_tokens,
    )


__all__ = ["AIConfigurationError", "ArchiveAnswerService", "MAX_LOGICAL_AI_CALLS"]
