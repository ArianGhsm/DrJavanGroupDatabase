from __future__ import annotations

from dataclasses import replace
import time

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.provider import AvalAIClient
from drjavanbot.ai.telemetry import TelemetryStore
from .model_policy import ModelDecision

class AvalAIIntelligenceProvider:
    """Provider-neutral QI protocol adapter for AvalAI/OpenAI-compatible models.

    Model and reasoning effort are selected per stage by ModelPolicy. Unlike the
    legacy DeepSeek V4 answer adapter this path does not force thinking off.
    """
    def __init__(self, *, api_key: str, base_config: AIConfig, telemetry: TelemetryStore | None = None) -> None:
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise ValueError("AI API key is missing or malformed")
        self._api_key=api_key; self._base_config=base_config; self._telemetry=telemetry
    def generate_json(self, *, system_prompt: str, user_prompt: str, decision: ModelDecision) -> str:
        config=replace(self._base_config, model=decision.model, reasoning_effort=decision.reasoning_effort)
        client=AvalAIClient(config); started=time.perf_counter(); result=None
        try:
            result=client.chat_json(api_key=self._api_key,request_type=str(decision.stage),system_prompt=system_prompt,user_prompt=user_prompt,max_output_tokens=decision.max_output_tokens)
            if str(result.finish_reason or "").casefold() in {"length","max_tokens","token_limit"}:
                raise ValueError("question intelligence output truncated")
        except Exception as exc:
            if self._telemetry is not None:
                self._telemetry.record(request_type="question_intelligence",stage=str(decision.stage),result_class="structured_output_failure" if isinstance(exc,ValueError) else "provider_failure",reason_code="output_truncated" if "truncated" in str(exc).casefold() else "provider_failed",logical_call=1,evidence_count=0,finish_reason=(result.finish_reason if result else None),model=(result.model if result else decision.model),latency_ms=(result.latency_ms if result else (time.perf_counter()-started)*1000),success=False,usage=(result.usage if result else None),error_class=type(exc).__name__)
            raise
        if self._telemetry is not None:
            self._telemetry.record(request_type="question_intelligence",stage=str(decision.stage),result_class="success",reason_code=None,logical_call=1,evidence_count=0,finish_reason=result.finish_reason,model=result.model or decision.model,latency_ms=result.latency_ms,success=True,usage=result.usage)
        return result.content

__all__=["AvalAIIntelligenceProvider"]
