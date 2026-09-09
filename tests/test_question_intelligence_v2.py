import json

from drjavanbot.intelligence.models import SourceType
from drjavanbot.intelligence.planning import (
    QUESTION_INTELLIGENCE_SYSTEM_PROMPT,
    QuestionIntelligenceEngine,
    parse_question_understanding,
)
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question


def test_cyst_prevalence_regression_understanding_is_not_recommendation():
    for question in (
        "کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟",
        "شایع‌ترین کیست ادنتوژنیک چیست؟",
        "most common odontogenic cyst?",
        "کدوم سیست اودنتوژنیک بیشتر دیده میشه؟",
    ):
        u = understand_question(question)
        assert u.domain == "dentistry"
        assert u.subdomain == "oral_pathology"
        assert "prevalence" in u.facets
        assert "recommendation" not in u.facets
        assert u.scientific_evidence_needed
        assert route_sources(u).selected_sources[0].source_type == SourceType.SCIENTIFIC


def test_salary_regressions_route_current_unless_user_explicitly_asks_archive():
    current = understand_question("حقوق دانشجوهای تازه فارغ التحصیل شده چقدره؟")
    assert {"salary", "career"}.issubset(current.facets)
    assert current.current_information_needed
    assert current.geography.country_code == "IR"
    assert current.geography.explicit is False
    assert any(item.canonical_id == "dentistry" and item.inferred for item in current.entities)
    assert route_sources(current).selected_sources[0].source_type == SourceType.CURRENT_WEB

    explicit = understand_question("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟")
    assert explicit.geography.country_code == "IR" and explicit.geography.explicit
    assert route_sources(explicit).selected_sources[0].source_type == SourceType.CURRENT_WEB

    archive = understand_question("گروه درباره حقوق دندانپزشکا چی گفته؟")
    assert archive.archive_specific and not archive.current_information_needed
    assert [x.source_type for x in route_sources(archive).selected_sources] == [SourceType.ARCHIVE]

    hybrid = understand_question("درباره حقوق دندانپزشک تازه‌کار هم نظر گروه رو بگو هم وضعیت الان ایران رو")
    routed = [x.source_type for x in route_sources(hybrid).selected_sources]
    assert hybrid.archive_specific and hybrid.current_information_needed
    assert routed[:2] == [SourceType.ARCHIVE, SourceType.CURRENT_WEB]


def test_question_intelligence_prompt_is_compact_semantic_schema_not_answer_prompt():
    assert "Do NOT answer" in QUESTION_INTELLIGENCE_SYSTEM_PROMPT
    assert "facets" in QUESTION_INTELLIGENCE_SYSTEM_PROMPT
    assert "current_information_needed" in QUESTION_INTELLIGENCE_SYSTEM_PROMPT
    assert "query_families" not in QUESTION_INTELLIGENCE_SYSTEM_PROMPT


def test_llm_parser_fails_closed_to_typed_schema_and_engine_has_deterministic_fallback():
    content = json.dumps({
        "schema_version": "question-intelligence-v2.0",
        "domain": "dentistry",
        "subdomain": "oral_pathology",
        "intent": "factual",
        "facets": ["prevalence"],
        "freshness": "evergreen",
        "archive_specific": False,
        "scientific_evidence_needed": True,
        "current_information_needed": False,
        "confidence_class": "high",
        "safety_class": "general",
    })
    parsed = parse_question_understanding(content, question="most common odontogenic cyst?")
    assert "prevalence" in parsed.facets
    assert parsed.scientific_evidence_needed

    class Broken:
        def generate_json(self, **_kwargs):
            return "{broken"

    understood, decision, fallback = QuestionIntelligenceEngine(provider=Broken()).understand("علائم و تشخیص و درمان periodontitis چیه؟")
    assert fallback and decision is not None
    assert {"signs", "diagnosis", "treatment"}.issubset(understood.facets)

def test_model_facets_are_ontology_locked_and_redundant_frequency_is_collapsed():
    import json
    from drjavanbot.intelligence.planning import parse_question_understanding
    payload={
        "schema_version":"question-intelligence-v2.0","domain":"dentistry","subdomain":"oral_pathology",
        "intent":"factual","facets":["prevalence","frequency","ranking"],"geography":{},
        "freshness":"evergreen","archive_specific":False,"scientific_evidence_needed":True,
        "current_information_needed":False,"confidence_class":"high","safety_class":"general"
    }
    u=parse_question_understanding(json.dumps(payload),question="most common odontogenic cyst?")
    assert u.facets == ("prevalence",)


def test_terse_english_acronym_with_persian_punctuation_stays_deterministic_archive():
    class MustNotRun:
        def generate_json(self, **_kwargs):
            raise AssertionError("QI model must not run for deterministic RCT lookup")

    understood, decision, fallback = QuestionIntelligenceEngine(provider=MustNotRun()).understand("RCT؟")
    assert understood.language_profile == "english"
    assert decision is None and fallback is False
    assert route_sources(understood).required_sources == (SourceType.ARCHIVE,)


def test_simple_mixed_archive_lookup_does_not_escalate_to_qi_model():
    class MustNotRun:
        def generate_json(self, **_kwargs):
            raise AssertionError("QI model must not run for deterministic archive lookup")

    understood, decision, fallback = QuestionIntelligenceEngine(provider=MustNotRun()).understand(
        "گروه درباره e.max چی گفته؟"
    )
    assert understood.language_profile == "mixed"
    assert decision is None and fallback is False
    assert route_sources(understood).required_sources == (SourceType.ARCHIVE,)
