from types import SimpleNamespace

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import AnswerResult
from drjavanbot.intelligence.models import SourceType
from drjavanbot.telegram.services import RuntimeServices


def _answer(mode: str, *, insufficient: bool, ai_calls: int = 0) -> AnswerResult:
    return AnswerResult(
        direct_answer=mode, key_findings=(), disagreements=(), practical_conclusion=None,
        confidence='low' if insufficient else 'medium', confidence_reason='test',
        cited_message_ids=() if insufficient else (1,), source_refs=() if insufficient else ('x',),
        evidence_used_count=0 if insufficient else 1, independent_authors_count=0 if insufficient else 1,
        insufficient_evidence=insufficient, safety_note_if_needed=None, source_mode=mode, ai_calls=ai_calls,
    )


def _state():
    return SimpleNamespace(selected_model=lambda default: default, set_provider_auth_failed=lambda value: None,
                           get_conversation_context=lambda uid: (_ for _ in ()).throw(KeyError()),
                           set_conversation_context=lambda uid, ctx: None)


def _runtime(tmp_path, *, with_db: bool = True, with_key: bool = False) -> RuntimeServices:
    data = tmp_path / 'data'; data.mkdir()
    if with_db:
        (data / 'archive.sqlite3').write_bytes(b'x')
    secrets = tmp_path / 'secrets'; secrets.mkdir()
    if with_key:
        (secrets / 'avalai_api_key').write_text('fake-key', encoding='utf-8')
    archive = tmp_path / 'archive'; archive.mkdir()
    return RuntimeServices(archive_dir=archive, data_dir=data, cache_dir=tmp_path / 'cache', secret_dir=secrets,
                           base_ai_config=AIConfig.from_env(), state=_state())


def _plan(*required):
    return SimpleNamespace(route=SimpleNamespace(required_sources=tuple(required)),
                           understanding=SimpleNamespace(entities=()), retrieval_requests=(),
                           planner_fallback_used=True, model_decision=None)


def _wire(monkeypatch, runtime, plan, *, archive: AnswerResult, multi: AnswerResult):
    calls = []

    class FakeArchive:
        def __init__(self, **_kwargs): pass
        def answer(self, question, **kwargs):
            calls.append(('archive', kwargs.get('precomputed_plan')))
            return archive

    class FakeMulti:
        def __init__(self, **_kwargs): pass
        def answer(self, plan, **kwargs):
            calls.append(('multi', kwargs.get('understanding_ai_calls')))
            return multi

    monkeypatch.setattr(runtime, '_intelligence_plan', lambda *a, **k: plan)
    monkeypatch.setattr('drjavanbot.telegram.services.ArchiveAnswerService', FakeArchive)
    monkeypatch.setattr('drjavanbot.telegram.services.MultiSourceAnswerService', FakeMulti)
    return calls


def test_archive_questions_are_answered_from_the_group_archive_only(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    archive = _answer('archive', insufficient=False)
    calls = _wire(monkeypatch, runtime, _plan(SourceType.ARCHIVE), archive=archive, multi=_answer('x', insufficient=False))
    assert runtime.answer('RCT?', user_id=None) is archive
    assert [name for name, _ in calls] == ['archive']


def test_scientific_questions_try_archive_first_and_keep_a_sufficient_archive_answer(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    archive = _answer('archive', insufficient=False)
    calls = _wire(monkeypatch, runtime, _plan(SourceType.SCIENTIFIC), archive=archive, multi=_answer('scientific', insufficient=False))
    assert runtime.answer('کدام کیست‌ها رایج‌ترند؟', user_id=None) is archive
    assert [name for name, _ in calls] == ['archive']


def test_scientific_fallback_runs_only_when_archive_has_no_answer(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    scientific = _answer('scientific', insufficient=False)
    calls = _wire(monkeypatch, runtime, _plan(SourceType.SCIENTIFIC),
                  archive=_answer('archive', insufficient=True, ai_calls=2), multi=scientific)
    assert runtime.answer('کدام کیست‌ها رایج‌ترند؟', user_id=None) is scientific
    # The fallback carries the archive attempt's AI calls into its accounting.
    assert calls == [('archive', None), ('multi', 2)]


def test_archive_insufficient_answer_is_kept_when_fallback_also_fails(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    archive = _answer('archive', insufficient=True)
    calls = _wire(monkeypatch, runtime, _plan(SourceType.SCIENTIFIC), archive=archive, multi=_answer('scientific', insufficient=True))
    assert runtime.answer('x', user_id=None) is archive
    assert [name for name, _ in calls] == ['archive', 'multi']


def test_time_sensitive_questions_use_current_sources_before_archive(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    current = _answer('current', insufficient=False)
    calls = _wire(monkeypatch, runtime, _plan(SourceType.CURRENT_WEB), archive=_answer('archive', insufficient=False), multi=current)
    assert runtime.answer('حقوق دندانپزشک چقدره؟', user_id=None) is current
    assert [name for name, _ in calls] == ['multi']


def test_time_sensitive_questions_fall_back_to_archive(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    archive = _answer('archive', insufficient=False)
    calls = _wire(monkeypatch, runtime, _plan(SourceType.CURRENT_WEB), archive=archive, multi=_answer('current', insufficient=True))
    assert runtime.answer('قیمت یونیت؟', user_id=None) is archive
    assert [name for name, _ in calls] == ['multi', 'archive']


def test_full_runtime_planning_keeps_canonical_routes_without_qi_model(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path, with_db=False, with_key=True)
    calls = []

    class GuardQIProvider:
        def __init__(self, **_kwargs): pass
        def generate_json(self, **_kwargs):
            calls.append(1)
            raise AssertionError('canonical deterministic queries must not invoke QI model')

    monkeypatch.setattr('drjavanbot.telegram.services.AvalAIIntelligenceProvider', GuardQIProvider)
    config = AIConfig.from_env()
    cases = {
        'کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟': (SourceType.SCIENTIFIC,),
        'حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟': (SourceType.CURRENT_WEB,),
        'گروه درباره e.max چی گفته؟': (SourceType.ARCHIVE,),
        'گروه درباره e.max چی گفته و شواهد علمی درباره‌اش چی میگه؟': (SourceType.ARCHIVE, SourceType.SCIENTIFIC),
        'RCT؟': (SourceType.ARCHIVE,),
        'درمان ضایعه خیالی zqv-99 چیه؟': (SourceType.SCIENTIFIC,),
    }
    for question, required in cases.items():
        plan = runtime._intelligence_plan(question, config, user_id=None)
        assert plan.route.required_sources == required
        assert plan.model_decision is None
        assert plan.planner_fallback_used is False
    assert calls == []


def test_follow_up_questions_search_the_archive_with_the_conversation_topic(monkeypatch, tmp_path):
    from drjavanbot.intelligence.conversation import ConversationQuestionContext
    from drjavanbot.intelligence.core import DentalIntelligenceCore
    from drjavanbot.intelligence.planning import QuestionIntelligenceEngine

    first = DentalIntelligenceCore(question_engine=QuestionIntelligenceEngine()).plan('ایمپلنت')
    context = ConversationQuestionContext.from_understanding(first.understanding).to_question_context()
    follow_up = DentalIntelligenceCore(question_engine=QuestionIntelligenceEngine(context=context)).plan('قیمتش چقدره؟')
    assert any(item.inferred for item in follow_up.understanding.entities)

    runtime = _runtime(tmp_path)
    calls = _wire(monkeypatch, runtime, follow_up, archive=_answer('archive', insufficient=False),
                  multi=_answer('current', insufficient=True))
    runtime.answer('قیمتش چقدره؟', user_id=None)
    archive_plan = next(plan for name, plan in calls if name == 'archive')
    assert archive_plan is not None
    assert any('implant' in ' '.join(group) or 'ایمپلنت' in ' '.join(group) for group in archive_plan.topic_anchor_groups)


def test_failing_external_fallback_keeps_the_archive_answer(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    archive = _answer('archive', insufficient=True)
    _wire(monkeypatch, runtime, _plan(SourceType.SCIENTIFIC), archive=archive, multi=_answer('x', insufficient=False))

    class ExplodingMulti:
        def __init__(self, **_kwargs): pass
        def answer(self, *_args, **_kwargs): raise TimeoutError('pubmed down')

    monkeypatch.setattr('drjavanbot.telegram.services.MultiSourceAnswerService', ExplodingMulti)
    assert runtime.answer('x', user_id=None) is archive
