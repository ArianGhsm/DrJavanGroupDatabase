from __future__ import annotations

import json
from datetime import datetime, timezone

from drjavanbot.ai.orchestrator import MAX_LOGICAL_AI_CALLS
from drjavanbot.ai.planner import (
    SearchFamily,
    SearchPlan,
    infer_question_aspects,
    parse_search_plan,
    plan_requires_deep_retrieval,
)
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _record(mid: int, order: int, text: str, author: str = "A") -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="گروه دکتر جوان/messages80.html",
        source_page=80,
        source_order=order,
        datetime=datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime_raw="01.01.2025 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        source_locator=f"گروه دکتر جوان/messages80.html#go_to_message{mid}",
    )


def _candidate(mid: int, order: int, text: str, author: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        message=_record(mid, order, text, author),
        local_score=7.0,
        matched_terms=tuple(text.casefold().split()[:2]),
        match_reasons=("normalized_tokens",),
    )


def test_planner_detects_age_population_facets_and_balances_family_schedule():
    question = "در بچه‌ها ارتودنسی رو در چه سنی باید استفاده کرد؟"
    payload = {
        "searchable": True,
        "intent": "timing_age",
        "core_concepts": ["ارتودنسی"],
        "aliases": ["orthodontic"],
        "optional_concepts": [],
        "entity_types": ["procedure"],
        "required_aspects": ["topic", "timing_age", "pediatric_population"],
        "query_families": [
            {"name": "topic", "queries": ["ارتودنسی", "orthodontic", "ortho", "ارتو"]},
            {"name": "timing", "queries": ["سن", "سالگی", "زمان شروع", "age"]},
            {"name": "population", "queries": ["کودک", "بچه", "اطفال", "pediatric"]},
            {"name": "stage", "queries": ["دندان مختلط", "mixed dentition", "فاز اول", "interceptive"]},
            {"name": "intersection", "queries": ["سن ارتودنسی", "شروع ارتودنسی", "ارتودنسی کودک", "early orthodontic"]},
            {"name": "experience", "queries": ["ارتودنسی تجربه", "orthodontic experience", "ارتودنسی شروع", "ارتودنسی بچه"]},
        ],
        "phrases": [], "exclude_terms": [], "low_information_terms": [], "reply_context": True,
    }
    plan = parse_search_plan(json.dumps(payload, ensure_ascii=False), question=question)
    assert {"timing_age", "pediatric_population"}.issubset(set(plan.required_aspects))
    first_round = [name for name, _ in plan.queries[:6]]
    assert len(set(first_round)) == 6
    assert len(plan.queries) <= 20
    assert plan_requires_deep_retrieval(plan)


def test_deterministic_facet_safety_net_survives_planner_omission():
    question = "برای بچه ارتودنسی از چه سنی شروع میشه؟"
    payload = {
        "searchable": True,
        "intent": "archive_lookup",
        "core_concepts": ["ارتودنسی"],
        "aliases": [], "optional_concepts": [], "entity_types": [],
        "query_families": [{"name": "topic", "queries": ["ارتودنسی"]}],
        "phrases": [], "exclude_terms": [], "low_information_terms": [], "reply_context": True,
    }
    plan = parse_search_plan(json.dumps(payload, ensure_ascii=False), question=question)
    names = {family.name for family in plan.query_families}
    assert "facet_timing_age" in names
    assert "facet_population" in names
    assert {"timing_age", "pediatric_population"}.issubset(set(infer_question_aspects(question)))


class FacetedBackend:
    def __init__(self):
        self.context_calls: list[tuple[int, int, int]] = []

    def search_many(self, queries):
        out = []
        for query in queries:
            raw = query.raw_query.casefold()
            if "ارتودنسی" in raw or "orthodont" in raw:
                out.append((_candidate(100, 100, "ارتودنسی در بیمار کم سن", "A"),))
            elif "سن" in raw or "سالگی" in raw or raw == "age":
                out.append((_candidate(101, 104, "در مورد سن شروع سوال شده", "B"),))
            elif "کودک" in raw or "بچه" in raw or "pediatric" in raw:
                out.append((_candidate(102, 107, "برای کودک این بحث مطرح شد", "C"),))
            else:
                out.append(())
        return tuple(out)

    def search(self, query):
        return self.search_many((query,))[0]

    def get_context(self, message, *, before=2, after=3, follow_reply=True):
        self.context_calls.append((message.message_id, before, after))
        return (
            _record(200 + message.message_id, message.source_order + 1, "ادامه بحث", "D"),
            _record(300 + message.message_id, message.source_order + 2, "۷ سالگی", "E"),
        )

    def get_message(self, message_id): return None
    def stats(self): return {"messages": 3, "index_version": "test"}


def test_different_facets_in_neighbor_messages_bridge_into_one_discussion():
    plan = SearchPlan(
        searchable=True,
        intent="timing_age",
        core_concepts=("ارتودنسی",),
        aliases=("orthodontic",),
        optional_concepts=(),
        entity_types=("procedure",),
        query_families=(
            SearchFamily("topic", ("ارتودنسی",)),
            SearchFamily("timing", ("سن", "سالگی")),
            SearchFamily("population", ("کودک", "بچه")),
        ),
        phrases=(), exclude_terms=(), low_information_terms=(), reply_context=True,
        required_aspects=("topic", "timing_age", "pediatric_population"),
    )
    backend = FacetedBackend()
    report = retrieve_with_plan(backend, plan, evidence_limit=24)
    assert report.conversation_bridges >= 2
    assert report.families_with_hits >= 3
    assert any("conversation_bridge" in candidate.match_reasons for candidate in report.candidates[:3])
    assert any(candidate.context for candidate in report.candidates[:3])
    # Bridged threads get a wider bounded context window than isolated hits.
    assert any(before >= 5 and after >= 6 for _, before, after in backend.context_calls)


def test_ai_call_budget_preserves_planner_rescue_synthesis_and_one_repair():
    assert MAX_LOGICAL_AI_CALLS == 4
