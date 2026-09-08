from drjavanbot.ai.config import AIConfig
from drjavanbot.intelligence.model_policy import ModelPolicy, ModelStage, ModelTier


def test_model_policy_uses_fast_for_simple_and_reasoning_for_ambiguous_multifacet():
    policy = ModelPolicy(fast_model="fast", reasoning_model="reasoning")
    simple = policy.select(ModelStage.QUESTION_UNDERSTANDING, facet_count=1)
    complex_ = policy.select(ModelStage.QUESTION_UNDERSTANDING, ambiguity=True, facet_count=3)
    assert simple.tier == ModelTier.FAST and not simple.thinking_enabled
    assert complex_.tier == ModelTier.REASONING and complex_.thinking_enabled
    assert complex_.reasoning_effort == "high"


def test_intelligence_feature_flags_default_off_for_production_compatibility(monkeypatch):
    for key in ("DRJAVAN_INTELLIGENCE_V2", "DRJAVAN_SOURCE_ROUTER_V2", "DRJAVAN_HYBRID_RETRIEVAL_V2"):
        monkeypatch.delenv(key, raising=False)
    config = AIConfig.from_env()
    assert not config.intelligence_v2
    assert not config.source_router_v2
    assert not config.hybrid_retrieval_v2

from drjavanbot.intelligence.planning import QuestionIntelligenceEngine

class _FailingIntelligenceProvider:
    def generate_json(self, **kwargs):
        raise RuntimeError("provider unavailable")


def test_question_intelligence_provider_failure_falls_back_deterministically():
    engine = QuestionIntelligenceEngine(provider=_FailingIntelligenceProvider())
    understanding, decision, fallback = engine.understand("most common odontogenic cyst?")
    assert fallback
    assert "prevalence" in understanding.facets
    assert decision is not None
