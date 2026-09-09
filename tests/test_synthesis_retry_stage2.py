import json

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.intelligence.fusion import RankedEvidence
from drjavanbot.intelligence.model_policy import ModelPolicy
from drjavanbot.intelligence.models import EvidenceItem, FacetCoverage, RequestedFactCoverage, SourceType
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.synthesis import GroundedMultiSourceSynthesizer
from drjavanbot.intelligence.understanding import understand_question


def _fixture():
    understanding = understand_question('شایع ترین کیست ادنتوژنیک چیست؟')
    route = route_sources(understanding)
    item = EvidenceItem(
        evidence_id='pubmed:1', source_type=SourceType.SCIENTIFIC, source_name='Journal', source_ref='PMID:1',
        title='Odontogenic cyst prevalence review',
        text='Radicular cyst was the most common odontogenic cyst in the review.',
        publication_year=2025, trust_tier='systematic_review', trust_score=.97,
    )
    ranked = (RankedEvidence(item, 1.0, 1.0, .97, 1.0, 1.0, .98),)
    coverage = RequestedFactCoverage(
        topic_present=True,
        requested_facets=(FacetCoverage('prevalence', True, 1.0, 'direct'),),
        requested_fact_supported=True, evidence_directness='direct', evidence_count=1, independent_sources=1,
        conflicts=False, freshness_satisfied=True, source_requirement_satisfied=True, answerable=True, reason_code='supported',
    )
    return understanding, route, ranked, coverage


def _result(content):
    return ProviderResult(content=content, model='deepseek-v4-flash', usage=UsageMetrics(), latency_ms=1.0, finish_reason='stop')


def test_synthesis_repairs_once_then_succeeds(monkeypatch):
    calls = []
    outputs = [
        _result('{not-json'),
        _result(json.dumps({'claims': [{'text': 'Radicular cyst was the most common odontogenic cyst.', 'support_ids': ['s001']}]})),
    ]

    class FakeClient:
        def __init__(self, config): pass
        def chat_json(self, **kwargs):
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr('drjavanbot.intelligence.synthesis.DeepSeekV4AvalAIClient', FakeClient)
    understanding, route, ranked, coverage = _fixture()
    answer = GroundedMultiSourceSynthesizer(
        api_key='test', base_config=AIConfig(), model_policy=ModelPolicy()
    ).synthesize(understanding, route, ranked, coverage)
    assert answer.insufficient_evidence is False
    assert answer.ai_calls == 2
    assert len(calls) == 2
    assert 'previous compact output failed local validation' in calls[1]['user_prompt']


def test_synthesis_stops_after_single_repair(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, config): pass
        def chat_json(self, **kwargs):
            calls.append(kwargs)
            return _result('{still-not-json')

    monkeypatch.setattr('drjavanbot.intelligence.synthesis.DeepSeekV4AvalAIClient', FakeClient)
    understanding, route, ranked, coverage = _fixture()
    answer = GroundedMultiSourceSynthesizer(
        api_key='test', base_config=AIConfig(), model_policy=ModelPolicy()
    ).synthesize(understanding, route, ranked, coverage)
    assert answer.insufficient_evidence is True
    assert answer.ai_calls == 2
    assert len(calls) == 2
    assert answer.confidence_reason.startswith('grounding_validation_failed:')
