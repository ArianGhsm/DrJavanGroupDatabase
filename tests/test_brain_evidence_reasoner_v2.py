from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pytest

from drjavanbot.ai.cache import ResponseCache
from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.evidence import build_evidence_pack
from drjavanbot.ai.models import EvidenceMessage, EvidencePack, ProviderResult, UsageMetrics
from drjavanbot.ai.orchestrator import ArchiveAnswerService, MAX_LOGICAL_AI_CALLS
from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.provider import ProviderTimeoutError, RateLimitError
from drjavanbot.ai.reasoning import (
    assess_answerability,
    compose_verified_answer,
    parse_claim_extraction,
    parse_verifier_output,
    select_verified_claims,
    semantic_candidates,
)
from drjavanbot.ai.validation import CitationValidationError
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.secrets import AVALAI_API_KEY_SECRET


def _pack(*texts: str) -> EvidencePack:
    messages = tuple(
        EvidenceMessage(
            source_ref=f"گروه دکتر جوان/messages1.html#go_to_message{index}",
            message_id=index,
            author=f"A{index}",
            datetime=None,
            source_file="گروه دکتر جوان/messages1.html",
            text=text,
            role="evidence" if index == 1 else "context",
            parent_source_ref=None if index == 1 else "گروه دکتر جوان/messages1.html#go_to_message1",
        )
        for index, text in enumerate(texts, 1)
    )
    return EvidencePack("سؤال", "سؤال", "simple", 100, messages)


def _payload(text: str, supports, *, kind: str = "answer") -> str:
    return json.dumps({
        "insufficient_evidence": False,
        "claims": [{
            "kind": kind,
            "text": text,
            "supports": [{"message_id": mid, "quote": quote} for mid, quote in supports],
        }],
    }, ensure_ascii=False)


def test_natural_grounded_paraphrase_requires_and_can_pass_entailment_verifier():
    pack = _pack("کار با این ماده راحت بود")
    extraction = parse_claim_extraction(_payload("استفاده از این ماده آسان توصیف شد", ((1, "کار با این ماده راحت بود"),)), pack)
    assert extraction.claims[0].needs_semantic_verification
    assert semantic_candidates(extraction.claims)
    verdicts = parse_verifier_output('{"verdicts":[{"claim_index":0,"entailed":true,"risk_ok":true}]}', (0,))
    verified = select_verified_claims(extraction.claims, verdicts)
    answer = compose_verified_answer(verified, pack, question="کار با ماده چطور بود؟")
    assert answer.direct_answer == "استفاده از این ماده آسان توصیف شد"
    assert answer.cited_message_ids == (1,)


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("مدل Z2500 خوب بود", "مدل Z250 خوب بود"),
        ("Filtek خوب بود", "این کامپوزیت خوب بود"),
        ("در 8 سالگی شروع شد", "در 7 سالگی شروع شد"),
    ],
)
def test_invented_number_age_or_brand_is_rejected_before_semantic_verifier(claim, quote):
    pack = _pack(quote)
    with pytest.raises(CitationValidationError):
        parse_claim_extraction(_payload(claim, ((1, quote),)), pack)


def test_reversed_comparison_and_negation_inversion_fail_closed():
    pack = _pack("A بهتر از B است")
    with pytest.raises(CitationValidationError):
        parse_claim_extraction(_payload("B بهتر از A است", ((1, "A بهتر از B است"),)), pack)
    pack = _pack("این ماده خوب نیست")
    with pytest.raises(CitationValidationError):
        parse_claim_extraction(_payload("این ماده خوب است", ((1, "این ماده خوب نیست"),)), pack)


def test_multiple_messages_can_jointly_support_one_atomic_claim():
    pack = _pack("ارتودنسی کودک", "۷ سالگی")
    extraction = parse_claim_extraction(_payload("ارتودنسی کودک: ۷ سالگی", ((1, "ارتودنسی کودک"), (2, "۷ سالگی"))), pack)
    assert not extraction.claims[0].needs_semantic_verification
    verified = select_verified_claims(extraction.claims)
    answer = compose_verified_answer(verified, pack, question="سن ارتودنسی کودک؟")
    assert answer.cited_message_ids == (1, 2)


def test_wrong_message_citation_and_model_memory_fact_are_rejected():
    pack = _pack("Alpha خوب بود", "Beta خوب بود")
    with pytest.raises(CitationValidationError):
        parse_claim_extraction(_payload("Alpha خوب بود", ((2, "Alpha خوب بود"),)), pack)
    with pytest.raises(CitationValidationError):
        parse_claim_extraction(_payload("Filtek خوب بود", ((1, "Alpha خوب بود"),)), pack)


def _record(mid: int, order: int, text: str, *, reply_to: int | None = None, author: str = "A") -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="گروه دکتر جوان/messages80.html",
        source_page=80,
        source_order=order,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="01.01.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        reply_to_message_id=reply_to,
        source_locator=f"گروه دکتر جوان/messages80.html#go_to_message{mid}",
    )


def _candidate(mid: int, order: int, text: str, *, context=(), reply_to=None, author="A") -> EvidenceCandidate:
    return EvidenceCandidate(
        _record(mid, order, text, reply_to=reply_to, author=author),
        8.0,
        tuple(text.casefold().split()[:2]),
        ("exact_phrase", "discussion_window") if context else ("exact_phrase",),
        tuple(context),
    )


def test_short_numeric_reply_keeps_topic_parent_in_evidence_pack():
    parent = _record(1, 100, "ارتودنسی کودک", author="Parent")
    reply = _candidate(2, 101, "۷ سالگی", context=(parent,), reply_to=1, author="Reply")
    pack = build_evidence_pack("سن ارتودنسی کودک", (reply,), AIConfig())
    by_id = {item.message_id: item for item in pack.messages}
    assert {1, 2}.issubset(by_id)
    assert by_id[1].role == "reply_context"


class _Report:
    def __init__(self, *, hit_names=(), bridges=0, windows=0):
        self.hit_family_names = tuple(hit_names)
        self.conversation_bridges = bridges
        self.discussion_windows = windows


def _faceted_plan() -> SearchPlan:
    return SearchPlan(
        searchable=True,
        intent="timing_age",
        core_concepts=("ارتودنسی",),
        aliases=(), optional_concepts=(), entity_types=("procedure",),
        query_families=(
            SearchFamily("topic", ("ارتودنسی",)),
            SearchFamily("timing", ("سن", "سالگی")),
            SearchFamily("population", ("کودک",)),
        ),
        phrases=(), exclude_terms=(), low_information_terms=(), reply_context=True,
        required_aspects=("topic", "timing_age", "pediatric_population"),
    )


def test_topical_evidence_with_requested_facet_missing_is_not_declared_answerable():
    pack = _pack("ارتودنسی مطرح شد")
    assessment = assess_answerability(pack, _faceted_plan(), _Report(hit_names=("topic",)))
    assert not assessment.answerable
    assert assessment.reason_code == "topic_found_facet_missing"


def test_facet_rich_fragmented_evidence_is_answerable_signal_for_bounded_rescue():
    pack = _pack("ارتودنسی کودک", "۷ سالگی")
    assessment = assess_answerability(pack, _faceted_plan(), _Report(hit_names=("topic", "timing", "population"), bridges=1))
    assert assessment.answerable
    assert assessment.reason_code == "fragmented_but_answerable"


class _SecretStore:
    def get_secret(self, name):
        return "test-key" if name == AVALAI_API_KEY_SECRET else None


class _Backend:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.index_version = "v1"
    def search_many(self, queries):
        return tuple(self.candidates for _ in queries)
    def search(self, query):
        return self.candidates
    def get_context(self, message, **kwargs):
        for candidate in self.candidates:
            if candidate.message.message_id == message.message_id:
                return candidate.context
        return ()
    def get_message(self, message_id):
        return None
    def stats(self):
        return {"index_version": self.index_version, "messages": len(self.candidates)}


class _Provider:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        request_type = kwargs["request_type"]
        if request_type == "search_plan":
            question = json.loads(kwargs["user_prompt"])["question"]
            content = json.dumps({
                "searchable": True,
                "intent": "archive_lookup",
                "core_concepts": [question],
                "aliases": [], "optional_concepts": [], "entity_types": [],
                "query_families": [{"name": "topic", "queries": [question]}],
                "phrases": [], "exclude_terms": [], "low_information_terms": [], "reply_context": True,
            }, ensure_ascii=False)
        elif request_type == "search_refinement":
            content = json.dumps({"query_families": []})
        else:
            value = self.scripted.pop(0)
            if isinstance(value, Exception):
                raise value
            content = value
        return ProviderResult(content, "deepseek-v4-flash", UsageMetrics(), 1.0)


class _FacetProvider(_Provider):
    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        request_type = kwargs["request_type"]
        if request_type == "search_plan":
            content = json.dumps({
                "searchable": True, "intent": "timing_age",
                "core_concepts": ["ارتودنسی"], "aliases": [], "optional_concepts": [], "entity_types": ["procedure"],
                "required_aspects": ["topic", "timing_age", "pediatric_population"],
                "query_families": [
                    {"name": "topic", "queries": ["ارتودنسی"]},
                    {"name": "timing", "queries": ["سن", "سالگی"]},
                    {"name": "population", "queries": ["کودک"]},
                ],
                "phrases": [], "exclude_terms": [], "low_information_terms": [], "reply_context": True,
            }, ensure_ascii=False)
        else:
            value = self.scripted.pop(0)
            if isinstance(value, Exception):
                raise value
            content = value
        return ProviderResult(content, "deepseek-v4-flash", UsageMetrics(), 1.0)


def test_facet_rich_first_insufficient_gets_one_bounded_rescue():
    parent = _record(1, 100, "ارتودنسی کودک", author="Parent")
    reply = _candidate(2, 101, "۷ سالگی", context=(parent,), reply_to=1, author="Reply")
    provider = _FacetProvider([
        json.dumps({"query_families": []}),
        json.dumps({"insufficient_evidence": True, "claims": []}),
        _payload("ارتودنسی کودک: ۷ سالگی", ((1, "ارتودنسی کودک"), (2, "۷ سالگی"))),
    ])
    answer = ArchiveAnswerService(backend=_Backend((reply,)), secret_store=_SecretStore(), config=AIConfig(), provider=provider).answer("سن ارتودنسی کودک؟")
    assert not answer.insufficient_evidence
    assert answer.ai_calls == MAX_LOGICAL_AI_CALLS == 4
    assert [call["request_type"] for call in provider.calls][-1] == "synthesis_recheck"


def test_malformed_extraction_and_verifier_are_bounded_and_fail_closed():
    candidate = _candidate(1, 10, "کار با ماده راحت بود")
    provider = _Provider(["not-json", "still-not-json"])
    answer = ArchiveAnswerService(backend=_Backend((candidate,)), secret_store=_SecretStore(), config=AIConfig(), provider=provider).answer("ماده")
    assert answer.insufficient_evidence and answer.ai_calls <= MAX_LOGICAL_AI_CALLS
    assert "structured_output_failed" in answer.confidence_reason

    provider = _Provider([
        _payload("استفاده از ماده آسان توصیف شد", ((1, "کار با ماده راحت بود"),)),
        "bad-verifier",
        "bad-verifier-again",
    ])
    answer = ArchiveAnswerService(backend=_Backend((candidate,)), secret_store=_SecretStore(), config=AIConfig(), provider=provider).answer("ماده")
    assert answer.insufficient_evidence and answer.ai_calls == 4
    assert "structured_output_failed" in answer.confidence_reason


@pytest.mark.parametrize("exc", [ProviderTimeoutError("timeout"), RateLimitError("rate")])
def test_provider_failures_are_controlled_and_not_semantic_insufficient(exc):
    candidate = _candidate(1, 10, "RCT")
    class Provider:
        def __init__(self): self.calls = 0
        def chat_json(self, **kwargs): self.calls += 1; raise exc
    provider = Provider()
    answer = ArchiveAnswerService(backend=_Backend((candidate,)), secret_store=_SecretStore(), config=AIConfig(), provider=provider).answer("RCT")
    assert answer.insufficient_evidence
    assert "provider_failed" in answer.confidence_reason
    assert answer.ai_calls == 1


def test_empty_content_is_structured_failure_not_true_insufficient():
    candidate = _candidate(1, 10, "RCT")
    class EmptyProvider:
        def __init__(self): self.calls = []
        def chat_json(self, **kwargs):
            self.calls.append(kwargs)
            return ProviderResult("", "deepseek-v4-flash", UsageMetrics(), 1.0)
    provider = EmptyProvider()
    answer = ArchiveAnswerService(backend=_Backend((candidate,)), secret_store=_SecretStore(), config=AIConfig(), provider=provider).answer("RCT")
    assert answer.insufficient_evidence
    assert "structured_output_failed" in answer.confidence_reason
    assert answer.ai_calls <= MAX_LOGICAL_AI_CALLS


def test_transient_provider_failure_is_not_cached_but_grounded_success_is():
    candidate = _candidate(1, 10, "RCT")
    success_payload = _payload("در گروه RCT مطرح شد", ((1, "RCT"),))
    class FlakyProvider:
        def __init__(self): self.calls = 0
        def chat_json(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderTimeoutError("timeout")
            if kwargs["request_type"] == "search_plan":
                q = json.loads(kwargs["user_prompt"])["question"]
                content = json.dumps({
                    "searchable": True, "intent": "archive_lookup", "core_concepts": [q],
                    "aliases": [], "optional_concepts": [], "entity_types": [],
                    "query_families": [{"name": "topic", "queries": [q]}],
                    "phrases": [], "exclude_terms": [], "low_information_terms": [], "reply_context": True,
                }, ensure_ascii=False)
            elif kwargs["request_type"] == "search_refinement":
                content = json.dumps({"query_families": []})
            else:
                content = success_payload
            return ProviderResult(content, "deepseek-v4-flash", UsageMetrics(), 1.0)
    provider = FlakyProvider()
    with tempfile.TemporaryDirectory() as td:
        cache = ResponseCache(Path(td) / "cache.sqlite3", ttl_seconds=3600)
        service = ArchiveAnswerService(backend=_Backend((candidate,)), secret_store=_SecretStore(), config=AIConfig(), provider=provider, cache=cache)
        first = service.answer("RCT")
        second = service.answer("RCT")
        third = service.answer("RCT")
        assert "provider_failed" in first.confidence_reason
        assert not second.cache_hit and not second.insufficient_evidence
        assert third.cache_hit and third.ai_calls == 0


def test_duplicate_text_is_suppressed_per_author_and_pii_redaction_remains_active():
    a = _candidate(1, 10, "کامپوزیت تماس 09121234567")
    b = _candidate(2, 40, "کامپوزیت تماس 09121234567")
    pack = build_evidence_pack("کامپوزیت", (a, b), AIConfig())
    assert len(pack.messages) == 1
    assert "09121234567" not in pack.messages[0].text
    assert "شماره تماس حذف شد" in pack.messages[0].text


def test_identical_text_from_independent_authors_is_preserved_as_corroboration():
    a = _candidate(1, 10, "کامپوزیت خوب بود", author="A")
    b = _candidate(2, 40, "کامپوزیت خوب بود", author="B")
    pack = build_evidence_pack("کامپوزیت", (a, b), AIConfig())
    assert {item.message_id for item in pack.messages} == {1, 2}
