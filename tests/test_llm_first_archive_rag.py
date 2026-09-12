import json

from drjavanbot.ai.config import AIConfig
from drjavanbot.intelligence.answerability import assess_requested_fact_coverage
from drjavanbot.intelligence.archive_provider import _context_is_promotable
from drjavanbot.intelligence.facets import detect_facets
from drjavanbot.intelligence.model_policy import ModelPolicy
from drjavanbot.intelligence.models import (
    EvidenceItem,
    QuestionIntent,
    SourceRequirement,
    SourceRoute,
    SourceSelection,
    SourceType,
)
from drjavanbot.intelligence.planning import QuestionIntelligenceEngine
from drjavanbot.intelligence.query_generation import generate_retrieval_requests
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.synthesis import _use_deterministic_archive_path
from drjavanbot.intelligence.understanding import understand_question
from drjavanbot.ai.discussion_types import _FamilyHit, _HitState


QUESTION = "کدوم برند کامپوزیت خوبه؟"


class _QIProvider:
    def __init__(self):
        self.calls = 0

    def generate_json(self, **_kwargs):
        self.calls += 1
        return json.dumps({
            "schema_version": "question-intelligence-v2.0",
            "domain": "dentistry",
            "subdomain": "restorative",
            "intent": "recommendation",
            "entities": [{"text": "کامپوزیت", "canonical": "composite resin", "type": "material"}],
            "facets": ["product", "recommendation"],
            "constraints": {"population": [], "temporal": [], "comparison_targets": [], "career_stage": [], "profession": []},
            "geography": {"country_code": None, "label": None, "explicit": False},
            "freshness": "unspecified",
            "archive_specific": True,
            "scientific_evidence_needed": False,
            "current_information_needed": False,
            "ambiguity": [],
            "confidence_class": "high",
            "safety_class": "general",
        }, ensure_ascii=False)


def test_practical_brand_question_uses_llm_understanding_and_archive_route():
    provider = _QIProvider()
    understanding, decision, fallback = QuestionIntelligenceEngine(
        provider=provider, model_policy=ModelPolicy()
    ).understand(QUESTION)

    assert provider.calls == 1
    assert decision is not None
    assert fallback is False
    assert understanding.intent == QuestionIntent.RECOMMENDATION
    assert route_sources(understanding).required_sources == (SourceType.ARCHIVE,)


def test_practical_brand_question_routes_to_archive_when_qi_provider_is_unavailable():
    understanding = understand_question(QUESTION)
    assert set(understanding.facets) == {"product", "recommendation"}
    assert understanding.scientific_evidence_needed is False
    assert route_sources(understanding).required_sources == (SourceType.ARCHIVE,)


def test_product_word_is_not_required_inside_recommendation_evidence():
    understanding = understand_question(QUESTION)
    route = SourceRoute(
        selected_sources=(SourceSelection(
            SourceType.ARCHIVE, 100, SourceRequirement.REQUIRED, "unspecified",
            "practical_archive_recommendation", "discussion_graph",
        ),),
        fallback_order=(SourceType.ARCHIVE,),
        rationale_codes=("practical_archive_recommendation",),
    )
    evidence = (EvidenceItem(
        evidence_id="archive:1", source_type=SourceType.ARCHIVE,
        source_name="group", source_ref="messages.html#1",
        text="من برای کامپوزیت با توکویاما کار کردم، خیلی خوب بود و راضی بودم.",
        author_or_org="member-1", independence_key="member-1",
    ),)

    coverage = assess_requested_fact_coverage(understanding, route, evidence)
    assert coverage.answerable
    assert tuple(item.facet for item in coverage.requested_facets) == ("recommendation",)


def test_practical_archive_recommendation_still_uses_llm_synthesis():
    understanding = understand_question(QUESTION)
    route = route_sources(understanding)
    assert not _use_deterministic_archive_path(understanding, route)


def test_plain_terse_archive_lookup_keeps_fast_deterministic_path():
    understanding = understand_question("RCT؟")
    route = route_sources(understanding)
    assert _use_deterministic_archive_path(understanding, route)


def test_recommendation_evidence_accepts_natural_experience_language():
    assert detect_facets(QUESTION) == ("recommendation", "product")


def test_generic_brand_entity_is_not_a_mandatory_archive_topic_anchor():
    provider = _QIProvider()
    understanding, _, _ = QuestionIntelligenceEngine(
        provider=provider, model_policy=ModelPolicy()
    ).understand(QUESTION)
    request = generate_retrieval_requests(
        understanding, route_sources(understanding)
    )[0]

    topic_families = {query.family for query in request.queries if query.purpose == "topic"}
    assert not any("brand" in family for family in topic_families)
    assert any("composite" in family for family in topic_families)
    assert len(topic_families) == 1
    assert any(
        query.text == "کامپوزیت پیشنهاد"
        for query in request.queries
        if query.family == "intersection_recommendation"
    )
    practical = {
        query.text
        for query in request.queries
        if query.family == "practical_recommendation"
    }
    assert practical == {
        "چه برند کامپوزیت پیشنهاد",
        "کامپوزیت برند خوب",
        "کامپوزیت راضی",
    }


def test_unqualified_or_recall_hits_do_not_manufacture_family_coverage():
    qualified = _FamilyHit(1, 0.98, 1.0, 6.0, True)
    unqualified = _FamilyHit(1, 0.98, 1.0, 3.8, False)
    noisy = _HitState(
        candidate=None,
        family_hits={"topic": qualified, **{f"noise_{i}": unqualified for i in range(6)}},
        matched_terms=set(),
        match_reasons={"fts_bm25"},
    )
    relevant = _HitState(
        candidate=None,
        family_hits={"topic": qualified, "recommendation": qualified},
        matched_terms=set(),
        match_reasons={"exact_phrase"},
    )

    assert relevant.base_score > noisy.base_score


def test_recommendation_reply_is_promoted_as_independently_citable_evidence():
    assert _context_is_promotable(
        "کاریزما کلاسیک کار کردم و راضی‌ام؛ از توکویاما هم راضی‌ام.",
        ("recommendation", "product"),
    )
    assert not _context_is_promotable(
        "این فقط یک پیام عمومی درباره کامپوزیت است.",
        ("recommendation", "product"),
    )
