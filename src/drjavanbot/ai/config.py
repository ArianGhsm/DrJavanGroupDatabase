from __future__ import annotations

from dataclasses import dataclass
import os

from drjavanbot.normalization import tokenize

DEFAULT_AVALAI_BASE_URL = "https://api.avalai.ir/v1"
DEFAULT_AVALAI_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True, slots=True)
class EvidenceBudget:
    name: str
    max_evidence_tokens: int
    max_messages: int
    max_output_tokens: int
    per_primary_chars: int
    per_context_chars: int


@dataclass(frozen=True, slots=True)
class AIConfig:
    base_url: str = DEFAULT_AVALAI_BASE_URL
    model: str = DEFAULT_AVALAI_MODEL
    timeout_seconds: float = 25.0
    max_retries: int = 2
    retry_base_seconds: float = 0.4
    reasoning_effort: str = "low"
    cache_ttl_seconds: int = 86_400
    # Search planning is deliberately much smaller than answer synthesis.
    planner_max_output_tokens: int = 320
    refinement_max_output_tokens: int = 220
    # Legacy field kept for config/backward compatibility; production semantic
    # retrieval no longer uses flat query expansion.
    expansion_max_output_tokens: int = 180
    simple_evidence_tokens: int = 2_500
    medium_evidence_tokens: int = 5_000
    complex_evidence_tokens: int = 8_000
    hard_evidence_tokens: int = 9_000
    simple_messages: int = 14
    medium_messages: int = 26
    complex_messages: int = 40
    hard_messages: int = 40
    simple_output_tokens: int = 800
    medium_output_tokens: int = 1_200
    complex_output_tokens: int = 1_800
    structured_retry_output_tokens: int = 2_400
    # Intelligence-v2 is opt-in until Stage 2 source adapters and multi-source
    # synthesis are production-ready. Compatibility mode remains the default.
    intelligence_v2: bool = False
    source_router_v2: bool = False
    hybrid_retrieval_v2: bool = False

    @classmethod
    def from_env(cls) -> "AIConfig":
        base_url = os.getenv("AVALAI_BASE_URL", DEFAULT_AVALAI_BASE_URL).strip().rstrip("/")
        model = os.getenv("AVALAI_MODEL", DEFAULT_AVALAI_MODEL).strip()
        if not base_url.startswith("https://"):
            raise ValueError("AVALAI_BASE_URL must use HTTPS")
        if not model:
            raise ValueError("AVALAI_MODEL cannot be empty")
        return cls(
            base_url=base_url,
            model=model,
            timeout_seconds=_float_env("DRJAVAN_AI_TIMEOUT_SECONDS", 25.0, 2.0, 120.0),
            max_retries=_int_env("DRJAVAN_AI_MAX_RETRIES", 2, 0, 4),
            retry_base_seconds=_float_env("DRJAVAN_AI_RETRY_BASE_SECONDS", 0.4, 0.0, 5.0),
            reasoning_effort=_choice_env("DRJAVAN_AI_REASONING_EFFORT", "low", {"low", "high", "max"}),
            cache_ttl_seconds=_int_env("DRJAVAN_AI_CACHE_TTL_SECONDS", 86_400, 60, 2_592_000),
            planner_max_output_tokens=_int_env("DRJAVAN_AI_PLANNER_MAX_OUTPUT_TOKENS", 320, 160, 700),
            refinement_max_output_tokens=_int_env("DRJAVAN_AI_REFINEMENT_MAX_OUTPUT_TOKENS", 220, 96, 500),
            expansion_max_output_tokens=_int_env("DRJAVAN_AI_EXPANSION_MAX_OUTPUT_TOKENS", 180, 64, 400),
            simple_evidence_tokens=_int_env("DRJAVAN_AI_SIMPLE_EVIDENCE_TOKENS", 2_500, 500, 9_000),
            medium_evidence_tokens=_int_env("DRJAVAN_AI_MEDIUM_EVIDENCE_TOKENS", 5_000, 1_000, 9_000),
            complex_evidence_tokens=_int_env("DRJAVAN_AI_COMPLEX_EVIDENCE_TOKENS", 8_000, 1_500, 9_000),
            hard_evidence_tokens=_int_env("DRJAVAN_AI_HARD_EVIDENCE_TOKENS", 9_000, 2_000, 12_000),
            simple_output_tokens=_int_env("DRJAVAN_AI_SIMPLE_OUTPUT_TOKENS", 800, 400, 4_000),
            medium_output_tokens=_int_env("DRJAVAN_AI_MEDIUM_OUTPUT_TOKENS", 1_200, 500, 4_000),
            complex_output_tokens=_int_env("DRJAVAN_AI_COMPLEX_OUTPUT_TOKENS", 1_800, 700, 6_000),
            structured_retry_output_tokens=_int_env("DRJAVAN_AI_STRUCTURED_RETRY_OUTPUT_TOKENS", 2_400, 800, 6_000),
            intelligence_v2=_bool_env("DRJAVAN_INTELLIGENCE_V2", False),
            source_router_v2=_bool_env("DRJAVAN_SOURCE_ROUTER_V2", False),
            hybrid_retrieval_v2=_bool_env("DRJAVAN_HYBRID_RETRIEVAL_V2", False),
        )

    def budget_for(self, question: str) -> EvidenceBudget:
        complexity = classify_question(question)
        if complexity == "simple":
            return EvidenceBudget("simple", min(self.simple_evidence_tokens, self.hard_evidence_tokens), min(self.simple_messages, self.hard_messages), self.simple_output_tokens, 1_500, 650)
        if complexity == "complex":
            return EvidenceBudget("complex", min(self.complex_evidence_tokens, self.hard_evidence_tokens), min(self.complex_messages, self.hard_messages), self.complex_output_tokens, 2_200, 1_000)
        return EvidenceBudget("medium", min(self.medium_evidence_tokens, self.hard_evidence_tokens), min(self.medium_messages, self.hard_messages), self.medium_output_tokens, 1_800, 850)

    def cache_signature(self) -> str:
        return ":".join(str(x) for x in (
            self.model, self.reasoning_effort, self.planner_max_output_tokens, self.refinement_max_output_tokens,
            self.simple_evidence_tokens, self.medium_evidence_tokens, self.complex_evidence_tokens,
            self.hard_evidence_tokens, self.simple_output_tokens, self.medium_output_tokens,
            self.complex_output_tokens, self.structured_retry_output_tokens,
            self.intelligence_v2, self.source_router_v2, self.hybrid_retrieval_v2,
        ))


def classify_question(question: str) -> str:
    tokens = tokenize(question)
    analytical = {
        "بهترین", "بهتر", "مقایسه", "چرا", "اختلاف", "تجربه", "توصیه", "پیشنهاد",
        "مزایا", "معایب", "کدام", "کدوم", "نظر", "نظرات", "جمع", "نتیجه", "compare", "why",
        "best", "recommend", "experience", "versus", "vs",
    }
    hits = sum(1 for token in tokens if token in analytical)
    if len(tokens) <= 6 and hits == 0:
        return "simple"
    if len(tokens) >= 18 or hits >= 2:
        return "complex"
    return "medium"


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else int(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _float_env(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else float(raw)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _choice_env(name: str, default: str, allowed: set[str]) -> str:
    value = (os.getenv(name) or default).strip().casefold()
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
