from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pytest

from drjavanbot.ai import AIConfig, ArchiveAnswerService, ResponseCache, TelemetryStore
from drjavanbot.ai.config import classify_question
from drjavanbot.ai.evidence import assess_retrieval, build_evidence_pack
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.ai.validation import CitationValidationError, ModelOutputError, parse_json_object, validate_answer_payload
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.secrets import AVALAI_API_KEY_SECRET


def record(mid: int, author: str, text: str, *, source_file: str = "گروه دکتر جوان/messages247.html") -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file=source_file,
        source_page=247,
        source_order=mid,
        datetime=datetime(2026, 4, 24, tzinfo=timezone.utc),
        datetime_raw="24.04.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        source_locator=f"{source_file}#go_to_message{mid}",
    )


def candidate(mid: int, author: str, text: str, score: float = 6.0, reasons=("exact_phrase",), context=()) -> EvidenceCandidate:
    return EvidenceCandidate(record(mid, author, text), score, ("rct",), tuple(reasons), tuple(context))


def supported_answer(*, text="در پیام‌های گروه RCT مطرح شده است.", supports=((1, "RCT"), (2, "RCT")), extra_claims=()):
    return {
        "insufficient_evidence": False,
        "claims": [
            {
                "kind": "answer",
                "text": text,
                "supports": [{"message_id": mid, "quote": quote} for mid, quote in supports],
            },
            *extra_claims,
        ],
    }


def insufficient_answer():
    return {"insufficient_evidence": True, "claims": []}


class MemorySecretStore:
    def __init__(self, value="test-secret-123"):
        self.data = {AVALAI_API_KEY_SECRET: value} if value else {}
    def get_secret(self, name): return self.data.get(name)
    def set_secret(self, name, value): self.data[name] = value
    def delete_secret(self, name): return self.data.pop(name, None) is not None
    def is_configured(self, name): return name in self.data


class MockBackend:
    def __init__(self, initial, expanded=None):
        self.initial = tuple(initial)
        self.expanded = tuple(expanded if expanded is not None else initial)
        self.index_version = "v1"
        self.calls = []
    def search(self, query):
        self.calls.append(query)
        q = query.raw_query.casefold()
        if any(term in q for term in ("درمان ریشه", "root canal", "refined", "corpus hint")):
            return self.expanded
        return self.initial
    def get_message(self, message_id): return None
    def get_context(self, message, **kwargs): return ()
    def stats(self): return {"index_version": self.index_version, "messages": 10}


class MockProvider:
    """Tests auto-supply a compact valid semantic planner before synthesis."""
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []
    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["request_type"] == "search_plan":
            question = json.loads(kwargs["user_prompt"])["question"]
            content = json.dumps({
                "searchable": True,
                "intent": "archive_lookup",
                "core_concepts": [question],
                "aliases": [],
                "optional_concepts": [],
                "entity_types": [],
                "query_families": [
                    {"name": "topic", "queries": [question]},
                    {"name": "context", "queries": [question + " تجربه"]},
                ],
                "phrases": [],
                "exclude_terms": [],
                "low_information_terms": [],
                "reply_context": True,
            }, ensure_ascii=False)
        else:
            content = self.contents.pop(0)
        return ProviderResult(content, "deepseek-v4-flash", UsageMetrics(100, 20, 30, 130, 12.5, "IRT", 1.0), 12.0)


def test_default_token_budgets_are_bounded():
    cfg = AIConfig()
    assert cfg.budget_for("RCT چیه").max_evidence_tokens == 2500
    assert cfg.budget_for("RCT چیه").max_output_tokens >= 800
    assert cfg.planner_max_output_tokens < cfg.medium_output_tokens
    b = cfg.budget_for("بهترین روش را مقایسه کن و اختلاف نظرات و تجربه ها و مزایا و معایب را کامل جمع بندی کن")
    assert b.max_evidence_tokens <= cfg.hard_evidence_tokens and b.max_messages <= cfg.hard_messages


def test_colloquial_kodom_is_not_misclassified_as_simple_lookup():
    assert classify_question("کدوم برند کامپوزیت خوبه؟") == "medium"


def test_evidence_pack_caps_and_redacts_obvious_pii():
    cfg = replace(AIConfig(), simple_evidence_tokens=500, hard_evidence_tokens=500)
    pack = build_evidence_pack("RCT", [candidate(1, "A", "تماس 09121234567 ایمیل x@example.com " + "متن " * 2000)], cfg)
    assert pack.estimated_tokens <= 500
    assert "09121234567" not in pack.messages[0].text
    assert "x@example.com" not in pack.messages[0].text


def test_retrieval_assessment_legacy_helper_still_handles_strong_exact_match():
    needs, reason = assess_retrieval([candidate(1, "A", "RCT", 6.0)])
    assert not needs and reason == "strong_top_match"


def test_two_call_normal_path_plans_then_synthesizes_grounded_claims():
    backend = MockBackend([candidate(1, "A", "RCT الف"), candidate(2, "B", "RCT ب")])
    provider = MockProvider([json.dumps(supported_answer(), ensure_ascii=False)])
    answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider).answer("RCT")
    assert answer.ai_calls == 2 and not answer.expansion_used and len(provider.calls) == 2
    assert [c["request_type"] for c in provider.calls] == ["search_plan", "synthesis"]
    assert answer.evidence_used_count == 2 and answer.independent_authors_count == 2
    assert answer.cited_message_ids == (1, 2)
    assert answer.grounded_claims and answer.grounded_claims[0].supports[0].quote == "RCT"


def test_citations_counts_and_confidence_are_derived_locally_not_from_model():
    pack = build_evidence_pack("RCT", [candidate(1, "A", "RCT الف"), candidate(2, "B", "RCT ب")], AIConfig())
    payload = supported_answer()
    payload.update({"confidence": "high", "cited_message_ids": [999], "source_refs": ["fake"]})
    answer = validate_answer_payload(payload, pack, question="RCT")
    assert answer.cited_message_ids == (1, 2)
    assert answer.evidence_used_count == 2
    assert answer.independent_authors_count == 2
    assert answer.confidence == "medium"
    assert "2 پیام" in answer.confidence_reason


def test_weak_retrieval_refines_then_synthesizes_in_three_calls():
    backend = MockBackend([], [candidate(1, "A", "درمان ریشه"), candidate(2, "B", "درمان ریشه")])
    provider = MockProvider([
        json.dumps({"query_families": [{"name": "refined", "queries": ["درمان ریشه", "root canal"]}]}, ensure_ascii=False),
        json.dumps(supported_answer(text="در پیام‌های گروه درمان ریشه مطرح شده است.", supports=((1, "درمان ریشه"), (2, "درمان ریشه"))), ensure_ascii=False),
    ])
    answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider).answer("موضوعی درباره عصب کشی")
    assert answer.ai_calls == 3 and answer.expansion_used
    assert [c["request_type"] for c in provider.calls] == ["search_plan", "search_refinement", "synthesis"]
    assert any("درمان ریشه" in q.raw_query for q in backend.calls)


def test_failed_refinement_returns_insufficient_without_synthesis_call():
    backend = MockBackend([], [])
    provider = MockProvider([json.dumps({"query_families": [{"name": "refined", "queries": ["abc"]}]})])
    answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider).answer("سؤال ناموجود")
    assert answer.insufficient_evidence and answer.ai_calls == 2 and len(provider.calls) == 2
    assert [c["request_type"] for c in provider.calls] == ["search_plan", "search_refinement"]


def test_global_valid_citation_cannot_cover_an_unsupported_claim():
    pack = build_evidence_pack("RCT", [candidate(1, "A", "RCT فقط")], AIConfig())
    payload = {
        "insufficient_evidence": False,
        "claims": [{"kind": "answer", "text": "برند ساختگی بهترین است", "supports": [{"message_id": 1, "quote": "عبارت ناموجود"}]}],
        "cited_message_ids": [1],
    }
    with pytest.raises(CitationValidationError):
        validate_answer_payload(payload, pack, question="RCT")


def test_support_quote_must_exist_in_the_same_message_not_another_evidence_message():
    pack = build_evidence_pack("کامپوزیت", [candidate(1, "A", "Alpha خوب بود"), candidate(2, "B", "Beta خوب بود")], AIConfig())
    payload = supported_answer(text="Alpha در گروه خوب توصیف شده.", supports=((2, "Alpha خوب بود"),))
    with pytest.raises(CitationValidationError):
        validate_answer_payload(payload, pack, question="کامپوزیت")


def test_product_or_number_token_cannot_be_invented_outside_question_or_verified_quote():
    pack = build_evidence_pack("کدوم کامپوزیت خوبه", [candidate(1, "A", "این کامپوزیت خوب بود")], AIConfig())
    payload = supported_answer(text="Filtek Z250 در گروه خوب توصیف شده.", supports=((1, "این کامپوزیت خوب بود"),))
    with pytest.raises(CitationValidationError):
        validate_answer_payload(payload, pack, question="کدوم کامپوزیت خوبه")


def test_product_token_is_allowed_when_it_is_in_verified_archive_quote():
    pack = build_evidence_pack("کدوم کامپوزیت خوبه", [candidate(1, "A", "Filtek Z250 خوب بود")], AIConfig())
    payload = supported_answer(text="در گروه Filtek Z250 خوب بود", supports=((1, "Filtek Z250 خوب بود"),))
    answer = validate_answer_payload(payload, pack, question="کدوم کامپوزیت خوبه")
    assert answer.cited_message_ids == (1,)
    assert "Filtek Z250" in answer.direct_answer


def test_each_finding_and_disagreement_requires_its_own_support():
    pack = build_evidence_pack("RCT", [candidate(1, "A", "RCT الف"), candidate(2, "B", "RCT ب")], AIConfig())
    payload = supported_answer(extra_claims=(
        {"kind": "finding", "text": "یافته مستند", "supports": [{"message_id": 1, "quote": "RCT الف"}]},
        {"kind": "disagreement", "text": "دیدگاه دیگری هم وجود دارد", "supports": []},
    ))
    with pytest.raises(CitationValidationError):
        validate_answer_payload(payload, pack, question="RCT")


def test_insufficient_model_prose_is_ignored_and_local_group_not_found_text_is_used():
    pack = build_evidence_pack("موضوع ناموجود", [candidate(1, "A", "پیام نامرتبط")], AIConfig())
    payload = {"insufficient_evidence": True, "claims": [], "direct_answer": "دانش عمومی مدل می‌گوید ..."}
    answer = validate_answer_payload(payload, pack, question="موضوع ناموجود")
    assert answer.insufficient_evidence
    assert answer.direct_answer == "در پیام‌های گروه، شواهد کافی برای پاسخ به این سؤال پیدا نشد."
    assert "دانش عمومی" not in answer.direct_answer
    assert answer.cited_message_ids == ()


def test_malformed_model_json_fails_closed():
    with pytest.raises(ModelOutputError):
        parse_json_object("not { valid")


def test_disagreement_changes_local_archive_coverage_confidence():
    pack = build_evidence_pack("کدام بهتر است", [candidate(1, "A", "الف بهتر است"), candidate(2, "B", "ب بهتر است")], AIConfig())
    payload = supported_answer(
        text="در گروه الف بهتر است و ب بهتر است",
        supports=((1, "الف بهتر است"), (2, "ب بهتر است")),
        extra_claims=({"kind": "disagreement", "text": "الف بهتر است و ب بهتر است", "supports": [{"message_id": 1, "quote": "الف بهتر است"}, {"message_id": 2, "quote": "ب بهتر است"}]},),
    )
    answer = validate_answer_payload(payload, pack, question="کدام بهتر است")
    assert answer.confidence == "medium"
    assert answer.disagreements
    assert "اختلاف‌نظر" in answer.confidence_reason


def test_clinical_question_gets_deterministic_archive_safety_note():
    pack = build_evidence_pack("درمان بیمار چیست", [candidate(1, "A", "درمان بیمار پیگیری شود")], AIConfig())
    payload = supported_answer(text="در گروه درمان بیمار پیگیری شود", supports=((1, "درمان بیمار پیگیری شود"),))
    answer = validate_answer_payload(payload, pack, question="درمان بیمار چیست")
    assert answer.safety_note_if_needed
    assert "گروه" in answer.safety_note_if_needed and "هوش مصنوعی" in answer.safety_note_if_needed


def test_cache_hit_removes_second_request_ai_calls():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    provider = MockProvider([json.dumps(supported_answer(), ensure_ascii=False)])
    with tempfile.TemporaryDirectory() as td:
        cache = ResponseCache(Path(td) / "cache.sqlite3", ttl_seconds=3600)
        service = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider, cache=cache)
        first = service.answer("RCT")
        second = service.answer("RCT")
        assert not first.cache_hit and second.cache_hit and second.ai_calls == 0
        assert len(provider.calls) == 2 and cache.stats().hits == 1 and cache.stats().misses == 1
        assert second.grounded_claims


def test_index_version_change_invalidates_answer_cache():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    response = json.dumps(supported_answer(), ensure_ascii=False)
    provider = MockProvider([response, response])
    with tempfile.TemporaryDirectory() as td:
        cache = ResponseCache(Path(td) / "cache.sqlite3", ttl_seconds=3600)
        service = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider, cache=cache)
        service.answer("RCT")
        backend.index_version = "v2"
        second = service.answer("RCT")
        assert not second.cache_hit and len(provider.calls) == 4


def test_telemetry_records_planner_and_synthesis_without_content():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    provider = MockProvider([json.dumps(supported_answer(), ensure_ascii=False)])
    with tempfile.TemporaryDirectory() as td:
        telemetry = TelemetryStore(Path(td) / "usage.sqlite3")
        ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider, telemetry=telemetry).answer("RCT")
        s = telemetry.summary()
        assert (s.calls, s.input_tokens, s.cached_input_tokens, s.output_tokens, s.cost_irt) == (2, 200, 40, 60, 25.0)
        assert b"test-secret-123" not in (Path(td) / "usage.sqlite3").read_bytes()


def test_service_without_key_fails_before_planner_or_synthesis():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    provider = MockProvider([])
    from drjavanbot.ai.orchestrator import AIConfigurationError
    service = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(None), config=AIConfig(), provider=provider)
    with pytest.raises(AIConfigurationError):
        service.answer("RCT")
    assert provider.calls == [] and backend.calls == []


def test_malformed_synthesis_retries_once_with_global_three_call_cap():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    provider = MockProvider(["not json", "still not json"])
    with tempfile.TemporaryDirectory() as td:
        telemetry = TelemetryStore(Path(td) / "usage.sqlite3")
        answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider, telemetry=telemetry).answer("RCT")
        s = telemetry.summary()
        assert answer.insufficient_evidence and answer.ai_calls == 3 and len(provider.calls) == 3
        assert "دوباره" in answer.direct_answer and s.calls == 3 and s.failures == 2
        assert provider.calls[2]["max_output_tokens"] > provider.calls[1]["max_output_tokens"]


def test_malformed_synthesis_retry_can_recover_with_larger_budget():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    provider = MockProvider(["", json.dumps(supported_answer(), ensure_ascii=False)])
    answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider).answer("RCT")
    assert "RCT" in answer.direct_answer and answer.ai_calls == 3 and len(provider.calls) == 3
    assert provider.calls[2]["max_output_tokens"] > provider.calls[1]["max_output_tokens"]
    assert "RETRY INSTRUCTION" in provider.calls[2]["system_prompt"]


def test_numeric_string_support_message_id_is_normalized_then_validated():
    backend = MockBackend([candidate(1, "A", "RCT"), candidate(2, "B", "RCT")])
    payload = supported_answer()
    payload["claims"][0]["supports"][0]["message_id"] = "1"
    provider = MockProvider([json.dumps(payload, ensure_ascii=False)])
    answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider).answer("RCT")
    assert answer.cited_message_ids == (1, 2) and answer.evidence_used_count == 2


def test_legacy_flat_supported_payload_is_rejected_instead_of_rendered():
    pack = build_evidence_pack("RCT", [candidate(1, "A", "RCT")], AIConfig())
    payload = {"direct_answer": "مستند", "confidence": "medium", "cited_message_ids": [1], "source_refs": [], "insufficient_evidence": False}
    with pytest.raises(CitationValidationError):
        validate_answer_payload(payload, pack, question="RCT")


def test_punctuation_only_question_never_calls_search_or_ai():
    backend = MockBackend([])
    provider = MockProvider([])
    answer = ArchiveAnswerService(backend=backend, secret_store=MemorySecretStore(), config=AIConfig(), provider=provider).answer("؟؟؟؟")
    assert answer.insufficient_evidence and answer.ai_calls == 0 and provider.calls == [] and backend.calls == []
