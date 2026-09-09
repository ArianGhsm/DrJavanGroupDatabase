from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os

MAX_MULTISOURCE_LOGICAL_AI_CALLS = 3


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
    fast_output_tokens: int = 480
    reasoning_output_tokens: int = 900
    synthesis_output_tokens: int = 1600
    verification_output_tokens: int = 900
    fast_reasoning_effort: str = "low"
    strong_reasoning_effort: str = "high"

    @classmethod
    def from_env(cls, *, default_model: str = "deepseek-v4-flash") -> "ModelPolicy":
        fast = (os.getenv("DRJAVAN_AI_FAST_MODEL") or default_model).strip()
        reasoning = (os.getenv("DRJAVAN_AI_REASONING_MODEL") or ("deepseek-v4-pro" if fast == "deepseek-v4-flash" else fast)).strip()
        return cls(
            fast_model=fast, reasoning_model=reasoning,
            fast_output_tokens=_int_env("DRJAVAN_AI_QI_FAST_OUTPUT_TOKENS", 480, 320, 1200),
            reasoning_output_tokens=_int_env("DRJAVAN_AI_QI_REASONING_OUTPUT_TOKENS", 900, 480, 1800),
            synthesis_output_tokens=_int_env("DRJAVAN_AI_SYNTHESIS_OUTPUT_TOKENS", 1600, 800, 3200),
            verification_output_tokens=_int_env("DRJAVAN_AI_VERIFICATION_OUTPUT_TOKENS", 900, 480, 1800),
            fast_reasoning_effort=_choice_env("DRJAVAN_AI_FAST_REASONING_EFFORT", "low"),
            strong_reasoning_effort=_choice_env("DRJAVAN_AI_STRONG_REASONING_EFFORT", "high"),
        )

    def select(self, stage: str, *, ambiguity: bool = False, facet_count: int = 0, mixed_language: bool = False, high_stakes: bool = False) -> ModelDecision:
        stage = str(stage)
        reasoning = (
            high_stakes or ambiguity or facet_count >= 3
            or (mixed_language and stage in {ModelStage.QUESTION_UNDERSTANDING, ModelStage.QUERY_REWRITE})
            or stage == ModelStage.VERIFICATION
        )
        if reasoning:
            max_tokens = self.synthesis_output_tokens if stage == ModelStage.SYNTHESIS else self.verification_output_tokens if stage == ModelStage.VERIFICATION else self.reasoning_output_tokens
            return ModelDecision(stage, ModelTier.REASONING, self.reasoning_model, self.strong_reasoning_effort, True, max_tokens)
        return ModelDecision(stage, ModelTier.FAST, self.fast_model, self.fast_reasoning_effort, False, self.fast_output_tokens)


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name); value = default if raw is None or not raw.strip() else int(raw)
    if not low <= value <= high: raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _choice_env(name: str, default: str) -> str:
    value = (os.getenv(name) or default).strip().casefold()
    if value not in {"low", "high", "max"}: raise ValueError(f"{name} must be low, high, or max")
    return value


__all__ = ["ModelTier", "ModelStage", "ModelDecision", "ModelPolicy", "MAX_MULTISOURCE_LOGICAL_AI_CALLS"]
