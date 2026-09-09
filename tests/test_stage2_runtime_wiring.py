from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import AnswerResult
from drjavanbot.intelligence.models import SourceType
from drjavanbot.telegram.services import RuntimeServices


def test_source_router_v2_uses_multisource_service_even_for_archive_only(monkeypatch, tmp_path):
    # Verify dispatch contract without network/AI. The archive DB only needs to exist
    # because RuntimeServices checks readiness before constructing the backend.
    data=tmp_path/'data'; data.mkdir(); (data/'archive.sqlite3').write_bytes(b'x')
    cache=tmp_path/'cache'; secrets=tmp_path/'secrets'; archive=tmp_path/'archive'; archive.mkdir()
    config=replace(AIConfig.from_env(), intelligence_v2=True, source_router_v2=True)
    state=SimpleNamespace(selected_model=lambda default: default, set_provider_auth_failed=lambda value: None,
                          get_conversation_context=lambda uid: (_ for _ in ()).throw(KeyError()),
                          set_conversation_context=lambda uid,ctx: None)
    runtime=RuntimeServices(archive_dir=archive,data_dir=data,cache_dir=cache,secret_dir=secrets,base_ai_config=config,state=state)
    plan=SimpleNamespace(
        route=SimpleNamespace(required_sources=(SourceType.ARCHIVE,)),
        understanding=SimpleNamespace(), retrieval_requests=(), planner_fallback_used=True, model_decision=None,
    )
    monkeypatch.setattr(runtime,'_intelligence_plan',lambda *a,**k: plan)
    expected=AnswerResult(direct_answer='ok',key_findings=(),disagreements=(),practical_conclusion=None,confidence='low',confidence_reason='test',cited_message_ids=(),source_refs=(),evidence_used_count=0,independent_authors_count=0,insufficient_evidence=True,safety_note_if_needed=None,source_mode='archive')
    class FakeMulti:
        def __init__(self,**kwargs): pass
        def answer(self,*args,**kwargs): return expected
    monkeypatch.setattr('drjavanbot.telegram.services.MultiSourceAnswerService',FakeMulti)
    assert runtime.answer('RCT?',user_id=None) is expected


def test_full_runtime_planning_keeps_canonical_routes_without_qi_model(monkeypatch, tmp_path):
    data=tmp_path/'data'; data.mkdir()
    cache=tmp_path/'cache'; secret_dir=tmp_path/'secrets'; archive=tmp_path/'archive'; archive.mkdir()
    secret_dir.mkdir(); (secret_dir/'avalai_api_key').write_text('fake-key',encoding='utf-8')
    config=replace(AIConfig.from_env(), intelligence_v2=True, source_router_v2=True, hybrid_retrieval_v2=True)
    state=SimpleNamespace(selected_model=lambda default: default,
                          set_provider_auth_failed=lambda value: None,
                          get_conversation_context=lambda uid: (_ for _ in ()).throw(KeyError()),
                          set_conversation_context=lambda uid,ctx: None)
    calls=[]
    class GuardQIProvider:
        def __init__(self,**_kwargs): pass
        def generate_json(self,**_kwargs):
            calls.append(1)
            raise AssertionError('canonical deterministic queries must not invoke QI model')
    monkeypatch.setattr('drjavanbot.telegram.services.AvalAIIntelligenceProvider',GuardQIProvider)
    runtime=RuntimeServices(archive_dir=archive,data_dir=data,cache_dir=cache,secret_dir=secret_dir,base_ai_config=config,state=state)
    cases={
        'کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟': (SourceType.SCIENTIFIC,),
        'حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟': (SourceType.CURRENT_WEB,),
        'گروه درباره e.max چی گفته؟': (SourceType.ARCHIVE,),
        'گروه درباره e.max چی گفته و شواهد علمی درباره‌اش چی میگه؟': (SourceType.ARCHIVE,SourceType.SCIENTIFIC),
        'RCT؟': (SourceType.ARCHIVE,),
        'درمان ضایعه خیالی zqv-99 چیه؟': (SourceType.SCIENTIFIC,),
    }
    for question, required in cases.items():
        plan=runtime._intelligence_plan(question,config,user_id=None)
        assert plan.route.required_sources == required
        assert plan.model_decision is None
        assert plan.planner_fallback_used is False
    assert calls == []


def test_full_runtime_rct_and_archive_emax_preserve_zero_qi_calls(monkeypatch, tmp_path):
    data=tmp_path/'data'; data.mkdir(); (data/'archive.sqlite3').write_bytes(b'x')
    cache=tmp_path/'cache'; secret_dir=tmp_path/'secrets'; archive=tmp_path/'archive'; archive.mkdir()
    secret_dir.mkdir(); (secret_dir/'avalai_api_key').write_text('fake-key',encoding='utf-8')
    config=replace(AIConfig.from_env(), intelligence_v2=True, source_router_v2=True, hybrid_retrieval_v2=True)
    state=SimpleNamespace(selected_model=lambda default: default,
                          set_provider_auth_failed=lambda value: None,
                          get_conversation_context=lambda uid: (_ for _ in ()).throw(KeyError()),
                          set_conversation_context=lambda uid,ctx: None)
    class GuardQIProvider:
        def __init__(self,**_kwargs): pass
        def generate_json(self,**_kwargs): raise AssertionError('QI model must not run')
    monkeypatch.setattr('drjavanbot.telegram.services.AvalAIIntelligenceProvider',GuardQIProvider)
    observed=[]
    class FakeMulti:
        def __init__(self,**_kwargs): pass
        def answer(self,plan,**kwargs):
            observed.append((plan.route.required_sources,kwargs.get('understanding_ai_calls')))
            return AnswerResult(direct_answer='ok',key_findings=(),disagreements=(),practical_conclusion=None,
                confidence='medium',confidence_reason='test',cited_message_ids=(1,),source_refs=('x',),
                evidence_used_count=1,independent_authors_count=1,insufficient_evidence=False,
                safety_note_if_needed=None,source_mode='archive',ai_calls=kwargs.get('understanding_ai_calls') or 0)
    monkeypatch.setattr('drjavanbot.telegram.services.MultiSourceAnswerService',FakeMulti)
    runtime=RuntimeServices(archive_dir=archive,data_dir=data,cache_dir=cache,secret_dir=secret_dir,base_ai_config=config,state=state)
    for question in ('RCT؟','گروه درباره e.max چی گفته؟'):
        result=runtime.answer(question,user_id=None)
        assert result.source_mode == 'archive'
        assert result.ai_calls == 0
    assert observed == [((SourceType.ARCHIVE,),0),((SourceType.ARCHIVE,),0)]
