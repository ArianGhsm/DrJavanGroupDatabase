from __future__ import annotations

from drjavanbot.ai.eval.golden import golden_cases
from drjavanbot.ai.eval.planning import quality_search_plan
from drjavanbot.ai.query_model import FamilyPurpose, RetrievalDepth
from drjavanbot.normalization import normalize_text


def _case(case_id: str):
    return next(case for case in golden_cases() if case.case_id == case_id)


def test_pediatric_quality_case_maps_to_typed_facets_and_preserves_frozen_queries():
    case = _case("ortho_pediatric_timing")
    plan = quality_search_plan(case)
    families = {family.name: family for family in plan.query_families}

    assert "timing_age" in plan.answer_facets
    assert "pediatric_population" in plan.answer_facets
    assert plan.required_aspects[:1] == ("topic",)
    assert families["topic"].anchor is True
    assert str(families["topic"].purpose) == str(FamilyPurpose.TOPIC)
    assert families["population"].anchor is False
    assert str(families["population"].purpose) == str(FamilyPurpose.POPULATION)
    assert str(families["timing"].purpose) == str(FamilyPurpose.FACET)
    assert plan.retrieval_policy.per_family_budget == 4
    assert plan.retrieval_policy.query_budget >= 11
    assert len(plan.queries) == 11


def test_short_acronym_quality_case_keeps_acronym_and_expansions_as_distinct_anchors():
    case = _case("short_rct")
    plan = quality_search_plan(case)
    families = {family.name: family for family in plan.query_families}

    assert set(families) == {"topic", "topic_aliases"}
    assert families["topic"].anchor is True
    assert families["topic_aliases"].anchor is True
    assert str(families["topic"].purpose) == str(FamilyPurpose.TOPIC)
    assert str(families["topic_aliases"].purpose) == str(FamilyPurpose.ALIAS)
    assert str(plan.retrieval_policy.depth) == str(RetrievalDepth.STANDARD)
    scheduled = tuple(normalize_text(query) for _family, query in plan.queries)
    assert scheduled == (normalize_text("RCT"), normalize_text("درمان ریشه"), normalize_text("root canal"))


def test_quality_adapter_never_invents_search_queries_or_fake_facet_names():
    for case in golden_cases():
        plan = quality_search_plan(case)
        frozen = {
            normalize_text(query)
            for _name, queries in case.query_families
            for query in queries
            if normalize_text(query)
        }
        scheduled = {normalize_text(query) for _family, query in plan.queries if normalize_text(query)}
        assert scheduled <= frozen
        assert all(not aspect.startswith("facet_") for aspect in plan.required_aspects)


def test_nonsearchable_quality_case_remains_query_free():
    plan = quality_search_plan(_case("noise_greeting"))
    assert plan.searchable is False
    assert plan.query_families == ()
    assert plan.queries == ()
    assert plan.required_aspects == ()
