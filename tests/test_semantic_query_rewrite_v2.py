from __future__ import annotations

import json

from drjavanbot.ai.planner import parse_search_plan
from drjavanbot.ai.planning_prompts import SEARCH_PLANNER_SYSTEM_PROMPT
from drjavanbot.ai.query_model import QUERY_MODEL_VERSION


def _payload(*, rewrite_queries):
    return {
        "schema_version": QUERY_MODEL_VERSION,
        "searchable": True,
        "normalized_intent": "timing_age",
        "topic_anchors": ["ارتودنسی"],
        "required_facets": ["timing_age", "pediatric_population"],
        "constraints": {
            "population": ["کودک"],
            "condition": [],
            "temporal": [],
            "comparison_targets": [],
        },
        "hints": {"aliases": [], "terminology": [], "colloquial": [], "typos": []},
        "query_families": [
            {
                "name": "topic",
                "purpose": "topic",
                "priority": 100,
                "anchor": True,
                "queries": ["ارتودنسی"],
            },
            {
                "name": "semantic_rewrite",
                "purpose": "intersection",
                "priority": 99,
                "anchor": True,
                "queries": rewrite_queries,
            },
        ],
        "negative_hints": [],
        "expected_evidence_pattern": "fragmented_discussion",
    }


def test_colloquial_pediatric_age_question_runs_multiple_semantic_rewrites():
    question = "چند سالگی ارتودنسی بچه بره دیره؟"
    rewrites = [
        "سن مناسب مراجعه کودک برای ارتودنسی",
        "چه سنی مراجعه برای ارتودنسی کودک دیر محسوب می شود",
        "ارتودنسی کودک سن مراجعه",
    ]
    plan = parse_search_plan(json.dumps(_payload(rewrite_queries=rewrites), ensure_ascii=False), question=question)

    scheduled = plan.queries
    scheduled_rewrites = [query for family, query in scheduled if family == "semantic_rewrite"]
    assert scheduled_rewrites == rewrites
    assert {"topic", "timing_age", "pediatric_population"}.issubset(plan.required_aspects)
    assert any(family.name == "semantic_rewrite" and family.anchor for family in plan.anchor_families)

    # Breadth is still protected: every selected family gets one first-round query
    # before rewrite variants consume additional budget.
    first_round = scheduled[: len(plan.query_families)]
    assert {family for family, _ in first_round} == {family.name for family in plan.query_families}


def test_semantic_rewrite_cannot_invent_an_age_threshold():
    question = "چند سالگی ارتودنسی بچه بره دیره؟"
    plan = parse_search_plan(
        json.dumps(
            _payload(
                rewrite_queries=[
                    "سن مناسب مراجعه کودک برای ارتودنسی",
                    "ارتودنسی کودک 7 سالگی",
                    "زمان مراجعه کودک برای ارتودنسی",
                ]
            ),
            ensure_ascii=False,
        ),
        question=question,
    )
    serialized = json.dumps(plan.to_dict(), ensure_ascii=False)
    assert "7" not in serialized
    assert "سن مناسب مراجعه کودک برای ارتودنسی" in serialized


def test_planner_contract_requires_semantic_rewrite_for_nontrivial_questions():
    assert QUERY_MODEL_VERSION == "query-understanding-v2"
    assert "semantic_rewrite" in SEARCH_PLANNER_SYSTEM_PROMPT
    assert "exactly 3 standalone semantically distinct search phrasings" in SEARCH_PLANNER_SYSTEM_PROMPT
    assert "not token permutations" in SEARCH_PLANNER_SYSTEM_PROMPT
    assert "MUST NOT introduce a clinical answer" in SEARCH_PLANNER_SYSTEM_PROMPT
