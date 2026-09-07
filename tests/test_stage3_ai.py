from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pytest

from drjavanbot.ai import AIConfig, ArchiveAnswerService, ResponseCache, TelemetryStore
from drjavanbot.ai.evidence import assess_retrieval, build_evidence_pack
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.ai.validation import CitationValidationError, ModelOutputError, parse_json_object, validate_answer_payload
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.secrets import AVALAI_API_KEY_SECRET


def record(mid: int, author: str, text: str, *, source_file: str = "گروه دکتر جوان/messages247.html") -> MessageRecord:
    return MessageRecord(
        message_id=mid, dom_id=f"message{mid}", source_file=source_file, source_page=247, source_order=mid,
        datetime=datetime(2026, 4, 24, tzinfo=timezone.utc), datetime_raw="24.04.2026 12:00:00 UTC+03:30",
        author=author, author_normalized=author.casefold(), text_raw=text, text_normalized=text.casefold(),
        source_locator=f"{source_file}#go_to_message{mid}",
    )


def candidate(mid: int, author: str, text: str, score: float = 6.0, reasons=("exact_phrase",), context=()) -> EvidenceCandidate:
    return EvidenceCandidate(record(mid, author, text), score, ("rct",), tuple(reasons), tuple(context))


def valid_answer(*, ids=(1, 2), refs=None, disagreements=None, confidence="high", insufficient=False):
    refs = refs or [f"گروه دکتر جوان/messages247.html#go_to_message{x}" for x in ids]
    return {"direct_answer":"جمع‌بندی مستند آرشیو","key_findings":["یافته اول"],"disagreements":disagreements or [],
            "practical_conclusion":"نتیجه عملی","confidence":confidence,"confidence_reason":"چند منبع مستقل",
            "cited_message_ids":list(ids),"source_refs":list(refs),"evidence_used_count":999,"independent_authors_count":999,
            "insufficient_evidence":insufficient,"safety_note_if_needed":None}


class MemorySecretStore:
    def __init__(self, value="test-secret-123"): self.data = {AVALAI_API_KEY_SECRET: value} if value else {}
    def get_secret(self, name): return self.data.get(name)
    def set_secret(self, name, value): self.data[name] = value
    def delete_secret(self, name): return self.data.pop(name, None) is not None
    def is_configured(self, name): return name in self.data


class MockBackend:
    def __init__(self, initial, expanded=None):
        self.initial=tuple(initial); self.expanded=tuple(expanded if expanded is not None else initial); self.index_version="v1"; self.calls=[]
    def search(self, query): self.calls.append(query); return self.expanded if query.variants else self.initial
    def get_message(self, message_id): return None
    def get_context(self, message, **kwargs): return ()
    def stats(self): return {"index_version":self.index_version,"messages":10}


class MockProvider:
    def __init__(self, contents): self.contents=list(contents); self.calls=[]
    def chat_json(self, **kwargs):
        self.calls.append(kwargs); content=self.contents.pop(0)
        return ProviderResult(content,"deepseek-v4-flash",UsageMetrics(100,20,30,130,12.5,"IRT",1.0),12.0)


def test_default_token_budgets_are_bounded():
    cfg=AIConfig(); assert cfg.budget_for("RCT چیه").max_evidence_tokens==2500
    b=cfg.budget_for("بهترین روش را مقایسه کن و اختلاف نظرات و تجربه ها و مزایا و معایب را کامل جمع بندی کن")
    assert b.max_evidence_tokens<=cfg.hard_evidence_tokens and b.max_messages<=cfg.hard_messages


def test_evidence_pack_caps_and_redacts_obvious_pii():
    cfg=replace(AIConfig(),simple_evidence_tokens=500,hard_evidence_tokens=500)
    pack=build_evidence_pack("RCT",[candidate(1,"A","تماس 09121234567 ایمیل x@example.com "+"متن "*2000)],cfg)
    assert pack.estimated_tokens<=500 and "09121234567" not in pack.messages[0].text and "x@example.com" not in pack.messages[0].text


def test_retrieval_assessment_avoids_expansion_for_strong_exact_match():
    needs,reason=assess_retrieval([candidate(1,"A","RCT",6.0)]); assert not needs and reason=="strong_top_match"


def test_one_call_normal_path():
    backend=MockBackend([candidate(1,"A","RCT الف"),candidate(2,"B","RCT ب")]); provider=MockProvider([json.dumps(valid_answer(),ensure_ascii=False)])
    answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider).answer("RCT")
    assert answer.ai_calls==1 and not answer.expansion_used and len(provider.calls)==1 and provider.calls[0]["request_type"]=="synthesis"
    assert answer.evidence_used_count==2 and answer.independent_authors_count==2


def test_fallback_expansion_then_synthesis_is_two_calls():
    backend=MockBackend([], [candidate(1,"A","درمان ریشه",6),candidate(2,"B","درمان ریشه",5)])
    provider=MockProvider([json.dumps({"variants":["درمان ریشه","root canal"]},ensure_ascii=False),json.dumps(valid_answer(),ensure_ascii=False)])
    answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider).answer("موضوعی درباره عصب کشی")
    assert answer.ai_calls==2 and answer.expansion_used and [c["request_type"] for c in provider.calls]==["expansion","synthesis"] and backend.calls[1].variants


def test_failed_expansion_returns_insufficient_without_synthesis_call():
    backend=MockBackend([],[]); provider=MockProvider([json.dumps({"variants":["abc"]})])
    answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider).answer("سؤال ناموجود")
    assert answer.insufficient_evidence and answer.ai_calls==1 and len(provider.calls)==1


def test_fake_citation_is_rejected():
    pack=build_evidence_pack("RCT",[candidate(1,"A","RCT")],AIConfig()); payload=valid_answer(ids=(999,),refs=[pack.messages[0].source_ref])
    with pytest.raises(CitationValidationError): validate_answer_payload(payload,pack,question="RCT")


def test_malformed_model_json_fails_closed():
    with pytest.raises(ModelOutputError): parse_json_object("not { valid")


def test_conflict_caps_high_confidence_to_medium():
    pack=build_evidence_pack("کدام بهتر است",[candidate(1,"A","الف"),candidate(2,"B","ب")],AIConfig())
    assert validate_answer_payload(valid_answer(disagreements=["دو نظر متفاوت وجود دارد"]),pack,question="کدام بهتر است").confidence=="medium"


def test_clinical_question_gets_archive_safety_note_if_model_omits_it():
    pack=build_evidence_pack("درمان بیمار چیست",[candidate(1,"A","الف"),candidate(2,"B","ب")],AIConfig())
    answer=validate_answer_payload(valid_answer(),pack,question="درمان بیمار چیست")
    assert answer.safety_note_if_needed and "آرشیو" in answer.safety_note_if_needed


def test_cache_hit_removes_second_ai_call():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")]); provider=MockProvider([json.dumps(valid_answer(),ensure_ascii=False)])
    with tempfile.TemporaryDirectory() as td:
        cache=ResponseCache(Path(td)/"cache.sqlite3",ttl_seconds=3600); service=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider,cache=cache)
        first=service.answer("RCT"); second=service.answer("RCT")
        assert not first.cache_hit and second.cache_hit and second.ai_calls==0 and len(provider.calls)==1 and cache.stats().hits==1 and cache.stats().misses==1


def test_index_version_change_invalidates_cache():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")]); response=json.dumps(valid_answer(),ensure_ascii=False); provider=MockProvider([response,response])
    with tempfile.TemporaryDirectory() as td:
        cache=ResponseCache(Path(td)/"cache.sqlite3",ttl_seconds=3600); service=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider,cache=cache)
        service.answer("RCT"); backend.index_version="v2"; second=service.answer("RCT")
        assert not second.cache_hit and len(provider.calls)==2


def test_telemetry_records_usage_without_content():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")]); provider=MockProvider([json.dumps(valid_answer(),ensure_ascii=False)])
    with tempfile.TemporaryDirectory() as td:
        telemetry=TelemetryStore(Path(td)/"usage.sqlite3"); ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider,telemetry=telemetry).answer("RCT")
        s=telemetry.summary(); assert (s.calls,s.input_tokens,s.cached_input_tokens,s.output_tokens,s.cost_irt)==(1,100,20,30,12.5)
        assert b"test-secret-123" not in (Path(td)/"usage.sqlite3").read_bytes()


def test_service_without_key_fails_before_synthesis():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")]); provider=MockProvider([])
    from drjavanbot.ai.orchestrator import AIConfigurationError
    service=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(None),config=AIConfig(),provider=provider)
    with pytest.raises(AIConfigurationError): service.answer("RCT")
    assert provider.calls==[]


def test_malformed_synthesis_retries_once_then_returns_safe_result():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")]); provider=MockProvider(["not json","still not json"])
    with tempfile.TemporaryDirectory() as td:
        telemetry=TelemetryStore(Path(td)/"usage.sqlite3")
        answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider,telemetry=telemetry).answer("RCT")
        s=telemetry.summary()
        assert answer.insufficient_evidence and answer.ai_calls==2 and len(provider.calls)==2
        assert "دوباره" in answer.direct_answer and s.calls==2 and s.failures==2


def test_malformed_synthesis_retry_can_recover():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")])
    provider=MockProvider(["",json.dumps(valid_answer(),ensure_ascii=False)])
    answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider).answer("RCT")
    assert answer.direct_answer=="جمع‌بندی مستند آرشیو" and answer.ai_calls==2 and len(provider.calls)==2


def test_numeric_string_citation_ids_are_normalized_then_validated():
    backend=MockBackend([candidate(1,"A","RCT"),candidate(2,"B","RCT")])
    payload=valid_answer(); payload["cited_message_ids"]=["1","2"]
    provider=MockProvider([json.dumps(payload,ensure_ascii=False)])
    answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider).answer("RCT")
    assert answer.cited_message_ids==(1,2) and answer.evidence_used_count==2


def test_punctuation_only_question_never_calls_search_or_ai():
    backend=MockBackend([]); provider=MockProvider([])
    answer=ArchiveAnswerService(backend=backend,secret_store=MemorySecretStore(),config=AIConfig(),provider=provider).answer("؟؟؟؟")
    assert answer.insufficient_evidence and answer.ai_calls==0 and provider.calls==[] and backend.calls==[]
