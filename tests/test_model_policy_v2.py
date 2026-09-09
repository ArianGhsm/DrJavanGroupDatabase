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
    understanding, decision, fallback = engine.understand("علائم و تشخیص و درمان periodontitis چیه؟")
    assert fallback
    assert {"signs", "diagnosis", "treatment"}.issubset(understanding.facets)
    assert decision is not None

def test_simple_synthesis_is_fast_but_complex_and_verification_use_reasoning():
    policy=ModelPolicy(fast_model="fast",reasoning_model="strong")
    simple=policy.select(ModelStage.SYNTHESIS,facet_count=1)
    hybrid_like=policy.select(ModelStage.SYNTHESIS,facet_count=3)
    verify=policy.select(ModelStage.VERIFICATION,facet_count=1)
    assert simple.tier==ModelTier.FAST and not simple.thinking_enabled
    assert hybrid_like.tier==ModelTier.REASONING and hybrid_like.thinking_enabled
    assert verify.tier==ModelTier.REASONING
