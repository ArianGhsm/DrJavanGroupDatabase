from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.models import ProviderResult, UsageMetrics
from drjavanbot.ai.orchestrator import ArchiveAnswerService
from drjavanbot.ai.planner import SearchFamily, SearchPlan, parse_search_plan
from drjavanbot.ai.planner_cache import SearchPlanCache
from drjavanbot.ai.provider import RateLimitError, ProviderTimeoutError
from drjavanbot.ai.retrieval import assess_planned_retrieval, retrieve_with_plan
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate
from drjavanbot.search.terms import informative_query, informative_tokens
from drjavanbot.secrets import AVALAI_API_KEY_SECRET


def _record(mid: int, author: str, text: str, *, reply: int | None = None, order: int | None = None) -> MessageRecord:
    return MessageRecord(
        message_id=mid, dom_id=f"message{mid}", source_file="گروه دکتر جوان/messages10.html",
        source_page=10, source_order=order if order is not None else mid,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc), datetime_raw="01.01.2026 12:00:00 UTC+03:30",
        author=author, author_normalized=author.casefold(), text_raw=text, text_normalized=text.casefold(),
        reply_to_message_id=reply, source_locator=f"گروه دکتر جوان/messages10.html#go_to_message{mid}",
    )


def _candidate(mid: int, author: str, text: str, *, score: float = 6.0, reply: int | None = None, context=()) -> EvidenceCandidate:
    return EvidenceCandidate(
        message=_record(mid, author, text, reply=reply), local_score=score,
        matched_terms=("کامپوزیت",), match_reasons=("exact_phrase",), context=tuple(context),
    )


class Secrets:
    def get_secret(self, name): return "key" if name == AVALAI_API_KEY_SECRET else None
    def set_secret(self, name, value): pass
    def delete_secret(self, name): return False
    def is_configured(self, name): return name == AVALAI_API_KEY_SECRET


class RoutingBackend:
    def __init__(self, routes: dict[str, tuple[EvidenceCandidate, ...]]):
        self.routes = routes; self.calls=[]; self.index_version="v1"
    def search(self, query):
        self.calls.append(query)
        normalized=query.raw_query.casefold()
        for key, value in self.routes.items():
            if key.casefold() in normalized:
                return value
        return ()
    def get_message(self, message_id): return None
    def get_context(self, message, **kwargs): return ()
    def stats(self): return {"index_version":self.index_version,"messages":100}


class SequenceProvider:
    def __init__(self, outputs): self.outputs=list(outputs); self.calls=[]
    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        value=self.outputs.pop(0)
        if isinstance(value, Exception): raise value
        return ProviderResult(str(value),"deepseek-v4-flash",UsageMetrics(20,0,10,30),5.0)


def _plan(*, searchable=True, families=None):
    return json.dumps({
        "searchable":searchable,"intent":"recommendation_comparison","core_concepts":["کامپوزیت"],
        "aliases":["composite"],"optional_concepts":["تجربه","پیشنهاد"],"entity_types":["product_or_brand"],
        "query_families":families if families is not None else [
            {"name":"topic","queries":["کامپوزیت","composite"]},
            {"name":"experience","queries":["کامپوزیت تجربه"]},
        ],
        "phrases":[],"exclude_terms":[],"low_information_terms":["کدوم","خوبه"],"reply_context":True,
    },ensure_ascii=False)


def _answer(ids=(1,2)):
    return json.dumps({
        "direct_answer":"بر اساس تجربه‌های بازیابی‌شده، چند گزینه مطرح شده‌اند.",
        "key_findings":["شواهد آرشیوی بررسی شد"],"disagreements":[],"practical_conclusion":None,
        "confidence":"medium","confidence_reason":"بیش از یک نویسنده",
        "cited_message_ids":list(ids),"source_refs":[],"insufficient_evidence":False,
    },ensure_ascii=False)


def test_low_information_words_never_drive_lexical_queries():
    assert "چرا" not in informative_tokens("چرا کامپوزیت ترک میخوره")
    assert informative_query("کدوم برند کامپوزیت خوبه؟") == "برند کامپوزیت"
    assert informative_query("چرا ریدی؟") == "ریدی"


def test_composite_search_plan_is_structured_multi_family_without_answering():
    plan=parse_search_plan(_plan(),question="کدوم برند کامپوزیت خوبه؟")
    assert plan.searchable and plan.intent=="recommendation_comparison"
    assert len(plan.query_families)>=2 and len(plan.queries)>=3
    assert "کامپوزیت" in plan.core_concepts and "product_or_brand" in plan.entity_types


def test_normal_semantic_path_is_planner_plus_synthesis_only():
    evidence=(_candidate(1,"A","کامپوزیت X تجربه خوبی داشت"),_candidate(2,"B","کامپوزیت Y را استفاده کردم"))
    backend=RoutingBackend({"کامپوزیت":evidence,"composite":evidence})
    provider=SequenceProvider([_plan(),_answer()])
    result=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider).answer("کدوم برند کامپوزیت خوبه؟")
    assert result.ai_calls==2 and not result.expansion_used
    assert [call["request_type"] for call in provider.calls]==["search_plan","synthesis"]
    assert len(backend.calls)>=2
    assert all("کدوم" not in call.raw_query and "خوبه" not in call.raw_query for call in backend.calls)


def test_non_searchable_noise_stops_before_local_retrieval():
    backend=RoutingBackend({"چرا":(_candidate(1,"A","چرا"),)})
    provider=SequenceProvider([_plan(searchable=False,families=[])])
    result=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider).answer("چرا ریدی؟")
    assert result.insufficient_evidence and result.ai_calls==1
    assert backend.calls==[] and [c["request_type"] for c in provider.calls]==["search_plan"]


def test_weak_first_pass_uses_archive_vocabulary_refinement_and_hard_three_call_cap():
    weak=(_candidate(9,"A","کامپوزیت",score=2.0,context=(_record(10,"A","ProductZ خیلی بهتر بود"),)),)
    strong=(_candidate(1,"A","ProductZ تجربه من"),_candidate(2,"B","ProductZ را پیشنهاد می‌کنم"))
    backend=RoutingBackend({"productz":strong,"کامپوزیت":weak})
    provider=SequenceProvider([
        _plan(families=[{"name":"topic","queries":["کامپوزیت"]}]),
        json.dumps({"query_families":[{"name":"corpus_refinement","queries":["ProductZ"]}]},ensure_ascii=False),
        _answer(),
    ])
    result=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider).answer("کدوم برند کامپوزیت خوبه؟")
    assert result.ai_calls==3 and result.expansion_used
    assert [c["request_type"] for c in provider.calls]==["search_plan","search_refinement","synthesis"]
    refinement=json.loads(provider.calls[1]["user_prompt"])
    assert any(term.casefold()=="productz" for term in refinement["observed_archive_vocabulary"])


def test_weak_path_never_makes_fourth_call_for_malformed_synthesis():
    weak=(_candidate(9,"A","کامپوزیت",score=2.0),)
    strong=(_candidate(1,"A","refined کامپوزیت"),_candidate(2,"B","refined کامپوزیت"))
    backend=RoutingBackend({"refined":strong,"کامپوزیت":weak})
    provider=SequenceProvider([
        _plan(families=[{"name":"topic","queries":["کامپوزیت"]}]),
        json.dumps({"query_families":[{"name":"refined","queries":["refined"]}]}),
        "not-json",
    ])
    result=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider).answer("کدوم برند کامپوزیت خوبه؟")
    assert result.ai_calls==3 and result.insufficient_evidence
    assert len(provider.calls)==3


def test_malformed_planner_falls_back_and_refines_without_model_memory_as_evidence():
    evidence=(_candidate(1,"A","کامپوزیت واقعی آرشیو"),_candidate(2,"B","کامپوزیت واقعی دوم"))
    backend=RoutingBackend({"کامپوزیت":evidence,"برند کامپوزیت":evidence,"refined":evidence})
    provider=SequenceProvider([
        "not-json",
        json.dumps({"query_families":[{"name":"refined","queries":["refined"]}]}),
        _answer(),
    ])
    result=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider).answer("برند کامپوزیت")
    assert result.ai_calls==3 and not result.insufficient_evidence
    assert [c["request_type"] for c in provider.calls]==["search_plan","search_refinement","synthesis"]


def test_planner_cache_is_bound_to_index_fingerprint(tmp_path: Path):
    evidence=(_candidate(1,"A","کامپوزیت A"),_candidate(2,"B","کامپوزیت B"))
    backend=RoutingBackend({"کامپوزیت":evidence})
    cache=SearchPlanCache(tmp_path/"plans.sqlite3",ttl_seconds=3600)
    provider=SequenceProvider([_plan(),_answer(),_answer()])
    service=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider,planner_cache=cache)
    first=service.answer("کامپوزیت"); second=service.answer("کامپوزیت")
    assert first.ai_calls==2 and second.ai_calls==1
    assert [c["request_type"] for c in provider.calls]==["search_plan","synthesis","synthesis"]
    backend.index_version="v2"
    provider.outputs.extend([_plan(),_answer()])
    third=service.answer("کامپوزیت")
    assert third.ai_calls==2 and provider.calls[-2]["request_type"]=="search_plan"


def test_malformed_planner_fallback_is_not_cached(tmp_path: Path):
    evidence=tuple(_candidate(i, f"A{i}", f"کامپوزیت evidence {i}") for i in range(1, 6))
    backend=RoutingBackend({"کامپوزیت":evidence})
    cache=SearchPlanCache(tmp_path/"plans.sqlite3",ttl_seconds=3600)
    provider=SequenceProvider(["not-json",_answer(),_plan(),_answer()])
    service=ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider,planner_cache=cache)

    first=service.answer("برند کامپوزیت")
    assert first.ai_calls==2
    assert cache.stats()["entries"]==0

    second=service.answer("برند کامپوزیت")
    assert second.ai_calls==2
    assert [call["request_type"] for call in provider.calls]==[
        "search_plan","synthesis","search_plan","synthesis"
    ]
    assert cache.stats()["entries"]==1


def test_planner_provider_timeout_and_rate_limit_do_not_loop():
    backend=RoutingBackend({})
    for error in (ProviderTimeoutError("timeout"),RateLimitError("rate")):
        provider=SequenceProvider([error])
        with pytest.raises(type(error)):
            ArchiveAnswerService(backend=backend,secret_store=Secrets(),config=AIConfig(),provider=provider).answer("کامپوزیت")
        assert len(provider.calls)==1 and provider.calls[0]["request_type"]=="search_plan"


def test_multi_query_fusion_preserves_reply_context_and_diversifies_authors():
    parent=_record(40,"A","کامپوزیت ProductQ")
    reply=_candidate(41,"A","من اینو خیلی دوست داشتم",reply=40,context=(parent,))
    other=_candidate(50,"B","ProductQ تجربه متفاوتی داشتم")
    backend=RoutingBackend({"کامپوزیت":(reply,),"productq":(reply,other)})
    plan=SearchPlan(
        searchable=True,intent="experience",core_concepts=("کامپوزیت",),aliases=(),optional_concepts=(),
        entity_types=("product",),query_families=(SearchFamily("topic",("کامپوزیت",)),SearchFamily("product",("ProductQ",))),
        phrases=(),exclude_terms=(),low_information_terms=(),reply_context=True,
    )
    report=retrieve_with_plan(backend,plan)
    assert report.candidates[0].context
    assert any("reply_context" in c.match_reasons for c in report.candidates)
    assert {c.message.author for c in report.candidates[:2]}=={"A","B"}


def test_post_filter_duplicate_queries_do_not_fake_family_coverage():
    evidence=(_candidate(1,"A","کامپوزیت اول"),_candidate(2,"B","کامپوزیت دوم"))
    backend=RoutingBackend({"کامپوزیت":evidence})
    plan=SearchPlan(
        searchable=True,intent="recommendation",core_concepts=("کامپوزیت",),aliases=(),optional_concepts=("خوب",),
        entity_types=("product",),query_families=(
            SearchFamily("topic",("کامپوزیت",)),
            SearchFamily("quality",("کامپوزیت خوب",)),
        ),phrases=(),exclude_terms=(),low_information_terms=("خوب",),reply_context=True,
    )
    report=retrieve_with_plan(backend,plan)
    assert report.query_runs==1
    assert report.duplicate_queries_skipped==1
    assert report.families_with_hits==1
    assert all("family_coverage:1" in item.match_reasons for item in report.candidates)
    needs_refinement,_=assess_planned_retrieval(report)
    assert needs_refinement
