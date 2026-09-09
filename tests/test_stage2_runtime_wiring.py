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
