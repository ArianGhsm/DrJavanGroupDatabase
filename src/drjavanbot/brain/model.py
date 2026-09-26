"""AvalAI/DeepSeek adapter for the archive brain.

Understanding and reranking are small, frequent calls on the fast model with
thinking disabled; the final answer uses the model the owner selected. A
malformed JSON reply is retried once; provider errors (auth, rate limit,
timeout) propagate so the Telegram layer can report them.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.provider import DeepSeekV4AvalAIClient
from drjavanbot.ai.telemetry import TelemetryStore
from drjavanbot.ai.validation import ModelOutputError, parse_json_object

FAST_MODEL = "deepseek-v4-flash"
_FAST_STAGES = frozenset({"understand", "rerank", "study"})


class AvalAIJSONModel:
    def __init__(self, *, api_key: str, config: AIConfig, telemetry: TelemetryStore | None = None) -> None:
        self.api_key = api_key
        self.answer_client = DeepSeekV4AvalAIClient(config)
        self.fast_client = DeepSeekV4AvalAIClient(replace(config, model=FAST_MODEL))
        self.telemetry = telemetry

    def complete_json(self, *, stage: str, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        client = self.fast_client if stage in _FAST_STAGES else self.answer_client
        last_error: ModelOutputError | None = None
        for attempt in (1, 2):
            result = client.chat_json(
                api_key=self.api_key, request_type=f"brain_{stage}", system_prompt=system,
                user_prompt=user, max_output_tokens=max_tokens,
            )
            try:
                parsed = parse_json_object(result.content)
            except ModelOutputError as exc:
                last_error = exc
                self._record(stage, result, attempt, success=False, error="ModelOutputError")
                continue
            self._record(stage, result, attempt, success=True)
            return parsed
        raise last_error or ModelOutputError("model output is not a JSON object")

    def _record(self, stage: str, result, attempt: int, *, success: bool, error: str | None = None) -> None:
        if self.telemetry is None:
            return
        self.telemetry.record(
            request_type=f"brain_{stage}", stage=stage, model=result.model, latency_ms=result.latency_ms,
            success=success, usage=result.usage, error_class=error, attempt=attempt,
            result_class="success" if success else "invalid_json", finish_reason=result.finish_reason,
        )


__all__ = ["AvalAIJSONModel", "FAST_MODEL"]
