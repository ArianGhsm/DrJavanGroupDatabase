from pathlib import Path

import pytest

from drjavanbot.ai.config import AIConfig, AIConfigurationError
from drjavanbot.ai.models import AnswerResult
from drjavanbot.brain.engine import BrainResult
from drjavanbot.storage import full_reindex
from drjavanbot.telegram.contracts import IndexNotReadyError
from drjavanbot.telegram.services import RuntimeServices
from drjavanbot.telegram.state import BotStateStore
from conftest import default_message, write_page


def _runtime(tmp_path: Path, *, key: bool = True, index: bool = True) -> RuntimeServices:
    archive = tmp_path / "archive"; archive.mkdir(parents=True)
    write_page(archive / "messages.html", default_message(1, "A", "روکش زیرکونیوم راکینگ دارد"))
    data = tmp_path / "data"; data.mkdir()
    if index:
        full_reindex(archive, data / "archive.sqlite3")
    secrets = tmp_path / "secrets"; secrets.mkdir()
    if key:
        (secrets / "avalai_api_key").write_text("fake-key", encoding="utf-8")
    return RuntimeServices(archive_dir=archive, data_dir=data, cache_dir=tmp_path / "cache", secret_dir=secrets,
                           base_ai_config=AIConfig.from_env(), state=BotStateStore(data / "state.sqlite3"))


def _answer(text: str) -> AnswerResult:
    return AnswerResult(direct_answer=text, key_findings=(), disagreements=(), practical_conclusion=None,
                        confidence="medium", confidence_reason="", cited_message_ids=(1,), source_refs=(),
                        evidence_used_count=1, independent_authors_count=1, insufficient_evidence=False,
                        safety_note_if_needed=None)


def test_single_answer_pipeline_has_no_version_flags(monkeypatch):
    for key in ("DRJAVAN_INTELLIGENCE_V2", "DRJAVAN_SOURCE_ROUTER_V2", "DRJAVAN_HYBRID_RETRIEVAL_V2"):
        monkeypatch.setenv(key, "true")
    config = AIConfig.from_env()
    for name in ("intelligence_v2", "source_router_v2", "hybrid_retrieval_v2"):
        assert not hasattr(config, name)
    assert "brain" in config.cache_signature()


def test_missing_key_and_missing_index_are_reported(tmp_path):
    with pytest.raises(AIConfigurationError):
        _runtime(tmp_path / "a", key=False).answer("سؤال")
    with pytest.raises(IndexNotReadyError):
        _runtime(tmp_path / "b", index=False).answer("سؤال")


def test_conversation_history_flows_between_questions(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    seen = []

    class FakeBrain:
        def __init__(self, **kwargs): pass
        def answer(self, question, *, history=(), progress=None):
            seen.append(tuple(t.standalone for t in history))
            return BrainResult(_answer("جواب " + question), standalone="فهمیده‌شده: " + question)

    monkeypatch.setattr("drjavanbot.telegram.services.ArchiveBrain", FakeBrain)
    runtime.answer("ایمپلنت", user_id=7)
    runtime.answer("و قیمتش؟", user_id=7)
    runtime.answer("سلام", user_id=8)
    assert seen == [(), ("فهمیده‌شده: ایمپلنت",), ()]
    turns = runtime.state.recent_turns(7)
    assert [t.question for t in turns] == ["ایمپلنت", "و قیمتش؟"]
    assert turns[-1].answer == "جواب و قیمتش؟"
