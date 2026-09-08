from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os


class ModelTier(StrEnum):
    FAST = "fast"
    REASONING = "reasoning"


class ModelStage(StrEnum):
    QUESTION_UNDERSTANDING = "question_understanding"
    QUERY_REWRITE = "query_rewrite"
    EVIDENCE_RERANK = "evidence_rerank"
    SYNTHESIS = "synthesis"
    VERIFICATION = "verification"


@dataclass(frozen=True, slots=True)
class ModelDecision:
    stage: str
    tier: str
    model: str
    reasoning_effort: str
    thinking_enabled: bool
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    fast_model: str = "deepseek-v4-flash"
    reasoning_model: str = "deepseek-v4-pro"
    fast_output_tokens: int = 520
    reasoning_output_tokens: int = 900

    @classmethod
    def from_env(cls, *, default_model: str = "deepseek-v4-flash") -> "ModelPolicy":
        fast = (os.getenv("DRJAVAN_AI_FAST_MODEL") or default_model).strip()
        reasoning = (os.getenv("DRJAVAN_AI_REASONING_MODEL") or fast).strip()
        return cls(
            fast_model=fast,
            reasoning_model=reasoning,
            fast_output_tokens=_int_env("DRJAVAN_AI_QI_FAST_OUTPUT_TOKENS", 520, 320, 1200),
            reasoning_output_tokens=_int_env("DRJAVAN_AI_QI_REASONING_OUTPUT_TOKENS", 900, 480, 1800),
        )

    def select(
        self,
        stage: str,
        *,
        ambiguity: bool = False,
        facet_count: int = 0,
        mixed_language: bool = False,
        high_stakes: bool = False,
    ) -> ModelDecision:
        stage = str(stage)
        reasoning = (
            high_stakes
            or ambiguity
            or facet_count >= 3
            or (mixed_language and stage in {ModelStage.QUESTION_UNDERSTANDING, ModelStage.QUERY_REWRITE})
            or stage in {ModelStage.SYNTHESIS, ModelStage.VERIFICATION}
        )
        if reasoning:
            return ModelDecision(
                stage=stage,
                tier=ModelTier.REASONING,
                model=self.reasoning_model,
                reasoning_effort="high",
                thinking_enabled=True,
                max_output_tokens=self.reasoning_output_tokens,
            )
        return ModelDecision(
            stage=stage,
            tier=ModelTier.FAST,
            model=self.fast_model,
            reasoning_effort="low",
            thinking_enabled=False,
            max_output_tokens=self.fast_output_tokens,
        )


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


__all__ = ["ModelTier", "ModelStage", "ModelDecision", "ModelPolicy"]
