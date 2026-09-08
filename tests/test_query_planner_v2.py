from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai import orchestrator
from drjavanbot.ai.planner import (
    PLANNER_VERSION,
    QUERY_MODEL_VERSION,
    deterministic_fallback_plan,
    parse_search_plan,
    plan_requires_deep_retrieval,
)
from drjavanbot.ai.planner_cache import SearchPlanCache
from drjavanbot.ai.planning_prompts import SEARCH_PLANNER_SYSTEM_PROMPT, search_planner_user_prompt
from drjavanbot.ai.query_model import EvidencePattern, FamilyPurpose, RetrievalDepth
from drjavanbot.ai.validation import ModelOutputError


def _typed_payload(*, topic: str, families=None, facets=(), hints=None, constraints=None):
    return {
        "schema_version": QUERY_MODEL_VERSION,
        "searchable": True,
        "normalized_intent": "lookup",
        "topic_anchors": [topic],
        "required_facets": list(facets),
        "constraints": constraints or {"population": [], "condition": [], "temporal": [], "comparison_targets": []},
        "hints": hints or {"aliases": [], "terminology": [], "colloquial": [], "typos": []},
        "query_families": families or [
            {"name": "topic", "purpose": "topic", "priority": 100, "anchor": True, "queries": [topic]},
        ],
        "negative_hints": [],
        "expected_evidence_pattern": "single_message",
    }


def test_composite_recommendation_is_deep_multisource_not_false_simple():
    plan = deterministic_fallback_plan("کدوم برند کامپوزیت خوبه؟")
    assert plan.searchable
    assert "recommendation" in plan.required_aspects
    assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.DEEP)
    assert plan.expected_evidence_pattern == str(EvidencePattern.MULTI_SOURCE)
    assert plan_requires_deep_retrieval(plan)
    assert len(plan.queries) <= 20


def test_pediatric_orthodontic_timing_keeps_topic_population_and_age_facets():
    plan = deterministic_fallback_plan("در بچه‌ها ارتودنسی رو در چه سنی باید استفاده کرد؟")
    assert {"topic", "timing_age", "pediatric_population"}.issubset(plan.required_aspects)
    assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.DEEP)
    assert plan.expected_evidence_pattern == str(EvidencePattern.FRAGMENTED_DISCUSSION)
    purposes = {str(family.purpose) for family in plan.query_families}
    assert str(FamilyPurpose.TOPIC) in purposes
    assert str(FamilyPurpose.FACET) in purposes
    assert str(FamilyPurpose.POPULATION) in purposes


def test_rct_and_emax_are_bounded_direct_lookups():
    for question in ("RCT?", "e.max"):
        plan = deterministic_fallback_plan(question)
        assert plan.searchable
        assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.DIRECT)
        assert not plan_requires_deep_retrieval(plan)
        assert plan.retrieval_policy.query_budget == 4
        assert len(plan.queries) <= 4
    assert "e" in deterministic_fallback_plan("e.max").core_concepts
    assert "max" in deterministic_fallback_plan("e.max").core_concepts


def test_mixed_persian_english_cause_query_is_preserved_and_deep():
    plan = deterministic_fallback_plan("چرا بعد RCT pain دارم؟")
    assert "cause_reason" in plan.required_aspects
    assert {"rct", "pain"}.issubset(set(plan.core_concepts))
    assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.DEEP)


def test_mechanical_typo_hint_is_generic_not_domain_dictionary():
    plan = deterministic_fallback_plan("کامپوزیییت خوبه؟")
    assert plan.searchable
    assert plan.typo_hints
    assert any("کامپوزیت" in value for value in plan.typo_hints)


def test_explicit_emax_zirconia_comparison_extracts_both_user_targets():
    plan = deterministic_fallback_plan("e.max یا زیرکونیا؟")
    assert "comparison" in plan.required_aspects
    assert len(plan.comparison_targets) == 2
    assert any("e max" in value for value in plan.comparison_targets)
    assert any("زیرکونیا" in value for value in plan.comparison_targets)
    assert plan.expected_evidence_pattern == str(EvidencePattern.MULTI_SOURCE)


def test_cause_method_and_quantity_facets_drive_deep_policy():
    cases = {
        "چرا بعد RCT درد داره؟": "cause_reason",
        "چطور رابردم بذارم؟": "method_how",
        "چه مقدار ماده لازمه؟": "quantity",
    }
    for question, facet in cases.items():
        plan = deterministic_fallback_plan(question)
        assert facet in plan.required_aspects
        assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.DEEP)


def test_dosage_query_never_accepts_model_invented_number():
    question = "دوز آموکسی سیلین چقدره؟"
    payload = _typed_payload(
        topic="آموکسی سیلین",
        facets=("dosage",),
        hints={"aliases": ["amoxicillin"], "terminology": ["500 mg"], "colloquial": [], "typos": []},
        families=[
            {"name": "topic", "purpose": "topic", "priority": 100, "anchor": True, "queries": ["آموکسی سیلین"]},
            {"name": "dose", "purpose": "facet", "priority": 90, "anchor": False, "queries": ["دوز", "500 mg"]},
        ],
    )
    plan = parse_search_plan(json.dumps(payload, ensure_ascii=False), question=question)
    serialized = json.dumps(plan.to_dict(), ensure_ascii=False)
    assert "dosage" in plan.required_aspects
    assert "500" not in serialized
    assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.DEEP)


def test_user_supplied_number_may_remain_search_hint_but_is_not_evidence():
    question = "آموکسی سیلین 500 mg؟"
    payload = _typed_payload(
        topic="آموکسی سیلین",
        hints={"aliases": [], "terminology": ["500 mg"], "colloquial": [], "typos": []},
        families=[{"name": "topic", "purpose": "topic", "priority": 100, "anchor": True, "queries": ["آموکسی سیلین 500 mg"]}],
    )
    plan = parse_search_plan(json.dumps(payload, ensure_ascii=False), question=question)
    assert any("500" in query for _, query in plan.queries)
    assert "evidence" not in plan.to_dict()


def test_non_searchable_noise_stays_non_searchable():
    plan = deterministic_fallback_plan("؟؟؟ !!!")
    assert not plan.searchable
    assert plan.queries == ()
    assert plan.required_aspects == ()


def test_malformed_and_truncated_planner_json_fail_for_orchestrator_fallback():
    with pytest.raises(ModelOutputError):
        parse_search_plan('{"searchable":true,"topic_anchors":[', question="RCT")
    fallback = deterministic_fallback_plan("RCT")
    assert fallback.searchable and fallback.queries


def test_unknown_schema_version_fails_closed():
    payload = _typed_payload(topic="RCT")
    payload["schema_version"] = "future-incompatible-v99"
    with pytest.raises(ModelOutputError):
        parse_search_plan(json.dumps(payload), question="RCT")


def test_duplicate_and_near_duplicate_queries_do_not_consume_budget():
    payload = _typed_payload(
        topic="e.max",
        facets=("comparison",),
        families=[
            {"name": "topic", "purpose": "topic", "priority": 100, "anchor": True, "queries": ["e.max زیرکونیا", "زیرکونیا e.max", "e.max زیرکونیا"]},
            {"name": "compare", "purpose": "facet", "priority": 90, "anchor": False, "queries": ["مقایسه", "compare", "compare"]},
        ],
    )
    plan = parse_search_plan(json.dumps(payload, ensure_ascii=False), question="e.max یا زیرکونیا؟")
    scheduled = [query for _, query in plan.queries]
    normalized_sets = {" ".join(sorted(query.replace(".", " ").casefold().split())) for query in scheduled}
    assert len(normalized_sets) == len(scheduled)
    assert len(plan.query_families) <= 8
    assert len(plan.queries) <= 20


def test_family_schedule_is_breadth_first_and_required_facets_cannot_be_starved():
    payload = _typed_payload(
        topic="ارتودنسی",
        families=[
            {"name": f"noise-{i}", "purpose": "alias", "priority": 10, "anchor": False, "queries": [f"variant-{i}-a", f"variant-{i}-b"]}
            for i in range(8)
        ],
    )
    plan = parse_search_plan(json.dumps(payload, ensure_ascii=False), question="در بچه‌ها ارتودنسی رو در چه سنی باید استفاده کرد؟")
    names = {family.name for family in plan.query_families}
    assert "topic" in names
    assert any("timing_age" in name for name in names)
    assert any("population" in name for name in names)
    first_round = [name for name, _ in plan.queries[: len(plan.query_families)]]
    assert len(first_round) == len(set(first_round))


def test_planner_cache_rejects_payload_with_stale_schema(tmp_path: Path):
    cache = SearchPlanCache(tmp_path / "plans.sqlite3")
    plan = deterministic_fallback_plan("RCT")
    cache.set("key", plan)
    assert cache.get("key", question="RCT") is not None

    with sqlite3.connect(tmp_path / "plans.sqlite3") as con:
        payload = plan.to_dict()
        payload["schema_version"] = "old-schema"
        con.execute("UPDATE search_plan_cache SET payload_json=? WHERE cache_key='key'", (json.dumps(payload),))
    assert cache.get("key", question="RCT") is None
    assert cache.stats()["entries"] == 0


def test_planner_cache_key_changes_for_index_and_planner_version(monkeypatch):
    cfg = AIConfig()
    first = orchestrator._planner_cache_key("rct", "index-a", cfg)
    second = orchestrator._planner_cache_key("rct", "index-b", cfg)
    assert first != second
    monkeypatch.setattr(orchestrator, "PLANNER_VERSION", PLANNER_VERSION + "-changed")
    third = orchestrator._planner_cache_key("rct", "index-a", cfg)
    assert third != first


def test_planning_prompt_is_compact_schema_driven_and_explicitly_non_evidentiary():
    user_payload = json.loads(search_planner_user_prompt("RCT?"))
    assert user_payload == {"schema_version": QUERY_MODEL_VERSION, "question": "RCT?"}
    assert QUERY_MODEL_VERSION in SEARCH_PLANNER_SYSTEM_PROMPT
    assert "Never answer" in SEARCH_PLANNER_SYSTEM_PROMPT
    assert "SEARCH HINTS ONLY" in SEARCH_PLANNER_SYSTEM_PROMPT
    assert len(SEARCH_PLANNER_SYSTEM_PROMPT) < 5000


def test_typed_plan_round_trip_preserves_policy_and_legacy_adapter_fields():
    plan = deterministic_fallback_plan("چرا بعد RCT درد داره؟")
    restored = type(plan).from_dict(plan.to_dict(), question="چرا بعد RCT درد داره؟")
    assert restored.schema_version == QUERY_MODEL_VERSION
    assert restored.required_aspects == plan.required_aspects
    assert restored.retrieval_policy.to_dict() == plan.retrieval_policy.to_dict()
    assert restored.core_concepts
    assert restored.query_families
