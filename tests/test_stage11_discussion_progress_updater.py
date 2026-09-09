from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.evidence import build_evidence_pack
from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.telegram.app_v3 import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig


ROOT = Path(__file__).resolve().parents[1]


def _record(mid: int, order: int, text: str, author: str = "A", *, reply_to: int | None = None) -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="گروه دکتر جوان/messages247.html",
        source_page=247,
        source_order=order,
        datetime=datetime(2026, 4, 24, tzinfo=timezone.utc),
        datetime_raw="24.04.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        reply_to_message_id=reply_to,
        source_locator=f"گروه دکتر جوان/messages247.html#go_to_message{mid}",
    )


def _candidate(mid: int, order: int, text: str, author: str = "A", *, score: float = 7.0, reply_to: int | None = None):
    return EvidenceCandidate(
        message=_record(mid, order, text, author, reply_to=reply_to),
        local_score=score,
        matched_terms=("کامپوزیت",),
        match_reasons=("exact_phrase",),
    )


def _plan() -> SearchPlan:
    return SearchPlan(
        searchable=True,
        intent="recommendation",
        core_concepts=("کامپوزیت",),
        aliases=(),
        optional_concepts=(),
        entity_types=("product",),
        query_families=(SearchFamily("topic", ("کامپوزیت",)),),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
    )


class DiscussionBackend:
    def __init__(self, values):
        self.values = tuple(values)
        self.context_calls = []

    def search_many(self, queries):
        return tuple(self.values for _ in queries)

    def search(self, query):
        return self.values

    def get_context(self, message, *, before=2, after=3, follow_reply=True):
        self.context_calls.append((message.message_id, before, after, follow_reply))
        return tuple(
            _record(9000 + message.message_id * 10 + i, message.source_order + i, f"context {i}", f"C{i}")
            for i in range(1, min(after, 4) + 1)
        )

    def stats(self):
        return {"messages": 10, "index_version": "test"}

    def get_message(self, message_id):
        return None


def test_nearby_retrieval_hits_open_bounded_discussion_windows():
    backend = DiscussionBackend([
        _candidate(1, 100, "کامپوزیت الف", "A"),
        _candidate(2, 105, "کامپوزیت ب", "B"),
        _candidate(3, 108, "کامپوزیت ج", "C"),
    ])
    report = retrieve_with_plan(backend, _plan(), evidence_limit=12)
    assert report.context_hydrated >= 3
    assert report.discussion_windows >= 3
    assert all(before >= 4 and after >= 5 for _, before, after, _ in backend.context_calls[:3])
    assert all("discussion_window" in candidate.match_reasons for candidate in report.candidates[:3])
    assert all(candidate.context for candidate in report.candidates[:3])


def test_isolated_long_hit_gets_small_context_not_unbounded_history():
    candidate = _candidate(
        1,
        100,
        "این یک پیام طولانی درباره کامپوزیت است که به تنهایی معنی کامل و مشخصی دارد",
        "A",
    )
    backend = DiscussionBackend([candidate])
    report = retrieve_with_plan(backend, _plan(), evidence_limit=12)
    assert report.context_hydrated == 1
    assert report.discussion_windows == 0
    assert backend.context_calls[0][1:3] == (2, 3)


def test_discussion_pack_reserves_context_without_exceeding_token_or_message_caps():
    contexts = tuple(
        _record(100 + i, 101 + i, f"پیام مرتبط {i} درباره تجربه استفاده", f"C{i}")
        for i in range(12)
    )
    values = []
    for i in range(8):
        base = _candidate(i + 1, 100 + i * 20, f"کامپوزیت تجربه {i}", f"A{i}")
        values.append(replace(
            base,
            match_reasons=(*base.match_reasons, "discussion_window", "context_available"),
            context=contexts,
        ))
    cfg = replace(
        AIConfig(),
        medium_evidence_tokens=1800,
        hard_evidence_tokens=1800,
        medium_messages=12,
        hard_messages=12,
    )
    pack = build_evidence_pack("کدوم کامپوزیت بهتره؟", values, cfg)
    assert len(pack.messages) <= 12
    assert pack.estimated_tokens <= 1800
    assert any(item.role in {"context", "reply_context"} for item in pack.messages)
    assert sum(1 for item in pack.messages if item.role == "evidence") >= 2


class FakeCache:
    def stats(self):
        return SimpleNamespace(entries=0, hits=0, misses=0, expired_entries=0)

    def clear(self):
        return 0


class FakeState:
    def is_allowed(self, user_id, owner_id): return True
    def consume_rate_slot(self, user_id, *, owner_id): return True
    def record_question(self, *args, **kwargs): pass
    def set_provider_auth_failed(self, value): pass
    def create_source_session(self, *args, **kwargs): return "session"
    def access_mode(self): return "owner_only"


class FakeAnswer:
    direct_answer = "پاسخ مستند"
    key_findings = ()
    disagreements = ()
    practical_conclusion = None
    confidence = "medium"
    confidence_reason = "دو پیام"
    cited_message_ids = ()
    source_refs = ()
    evidence_used_count = 2
    independent_authors_count = 2
    insufficient_evidence = False
    safety_note_if_needed = None
    cache_hit = False
    ai_calls = 2
    grounded_claims = ()


class ProgressiveServices:
    cache = FakeCache()
    def ai_configured(self): return True
    def answer_with_progress(self, question, progress):
        progress("planning", {"cached": False})
        progress("searching", {"query_count": 5, "family_count": 3})
        progress("context", {"candidate_count": 12, "author_count": 7, "discussion_windows": 3, "context_hydrated": 5})
        progress("synthesizing", {"evidence_messages": 9, "evidence_authors": 6, "estimated_tokens": 1200})
        progress("validating", {"evidence_messages": 9, "evidence_authors": 6})
        progress("done", {"evidence_used": 2, "authors": 2, "insufficient": False})
        return FakeAnswer()
    def source_details(self, ids, refs): return []


class FakeAPI:
    def __init__(self):
        self.sent=[]; self.edited=[]; self.deleted=[]; self.actions=[]
    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, str(text), kwargs)); return {"message_id": 77 if len(self.sent) == 1 else 78}
    def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edited.append((chat_id, message_id, str(text), kwargs)); return {}
    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id)); return True
    def send_chat_action(self, chat_id, action="typing"):
        self.actions.append((chat_id, action)); return True


def test_progressive_app_shows_real_stages_and_deletes_status_after_answer():
    api=FakeAPI()
    app=TelegramBotApp(
        api=api, owner_id=42, services=ProgressiveServices(), state=FakeState(),
        config=TelegramConfig(progress_ui_enabled=True),
    )
    app._handle_question(42, 42, "کدوم کامپوزیت خوبه؟")
    assert api.sent
    assert "در حال بررسی سؤال و منابع" in api.sent[0][1]
    combined="\n".join(item[2] for item in api.edited)
    assert "جست" in combined
    assert "گفت" in combined or "context" in combined
    assert "تأیید" in combined or "شواهد" in combined
    assert any("پاسخ مستند" in item[1] for item in api.sent[1:])
    assert (42,77) in api.deleted
    assert len(api.actions) >= 2


def test_progress_ui_never_presents_itself_as_model_chain_of_thought():
    api=FakeAPI()
    app=TelegramBotApp(
        api=api, owner_id=42, services=ProgressiveServices(), state=FakeState(),
        config=TelegramConfig(progress_ui_enabled=True),
    )
    app._handle_question(42,42,"سؤال")
    text="\n".join([x[1] for x in api.sent]+[x[2] for x in api.edited]).casefold()
    assert "chain-of-thought داخلی" not in text
    assert "reasoning خصوصی" not in text
    assert "prompt" not in text


def _load_updater():
    path=ROOT/"deploy/update_engine_v2.py"
    spec=importlib.util.spec_from_file_location("drjavan_stage11_updater",path)
    module=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
    return module


def test_pip_install_uses_cache_long_read_timeout_and_bounded_retry(tmp_path, monkeypatch):
    module=_load_updater()
    cache=tmp_path/"pip-cache"
    monkeypatch.setattr(module,"PIP_CACHE_DIR",cache)
    monkeypatch.setattr(module.time,"sleep",lambda *_: None)
    calls=[]; progress=[]
    def fake_run(cmd,**kwargs):
        calls.append((list(cmd),dict(kwargs)))
        if len(calls)==1:
            raise module.UpdateFailure("simulated network timeout")
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    monkeypatch.setattr(module,"_run",fake_run)
    monkeypatch.setattr(module,"_progress",lambda *args,**kwargs: progress.append((args,kwargs)))
    module._pip_install(
        Path("/venv/bin/python"),["-r","requirements-dev.lock"],cwd=tmp_path,
        request_id="req",current_sha="a"*40,target_sha="b"*40,
    )
    assert len(calls)==2
    cmd,kwargs=calls[-1]
    assert "--timeout" in cmd and str(module.PIP_NETWORK_TIMEOUT_SECONDS) in cmd
    assert "--retries" in cmd and str(module.PIP_RETRIES) in cmd
    assert kwargs["timeout"]==module.PIP_PROCESS_TIMEOUT_SECONDS
    assert kwargs["env"]["PIP_CACHE_DIR"]==str(cache)
    assert kwargs["env"]["PIP_NO_INPUT"]=="1"
    assert progress and "تلاش 2/2" in progress[-1][0][5]


def test_updater_dependency_install_is_pre_switch_and_single_locked_pass():
    source=(ROOT/"deploy/update_engine_v2.py").read_text(encoding="utf-8")
    update=source[source.index("def _update"):source.index("def _rollback")]
    assert update.index("release = _prepare_release") < update.index('["systemctl", "stop", SERVICE]')
    prepare=source[source.index("def _prepare_release"):source.index("def _run_stage_gates")]
    assert 'requirements-dev.lock' in prepare
    assert '"-r", "requirements.lock"' not in prepare
    # Full pytest/Quality Lab is exact-SHA CI responsibility, not repeated by VPS.
    stage=source[source.index("def _run_stage_gates"):source.index("def _github_ci_status")]
    assert "pytest" not in stage and "quality-eval" not in stage
