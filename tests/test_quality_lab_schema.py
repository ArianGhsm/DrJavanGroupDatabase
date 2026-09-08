from drjavanbot.ai.eval import REPORT_SCHEMA_VERSION, golden_cases


def test_golden_suite_is_diverse_bounded_and_pii_safe_metadata():
    cases = golden_cases()
    assert 30 <= len(cases) <= 50
    categories = {case.category for case in cases}
    required = {
        "direct_topic_product", "recommendation_experience", "comparison",
        "age_timing_population", "cause_mechanism", "method_technique",
        "quantity_dose_like", "symptom_procedure_relation", "short_acronym",
        "mixed_persian_english", "typo_punctuation", "colloquial_persian",
        "fragmented_telegram_discussion", "reply_parent_answer",
        "correction_disagreement", "no_evidence_sentinel", "low_information_noise",
        "privacy_sensitive_case", "duplicate_query_family", "author_diversity",
    }
    assert required <= categories
    assert REPORT_SCHEMA_VERSION == "quality-lab-v2.0"
    assert len({case.case_id for case in cases}) == len(cases)
    for case in cases:
        assert "messages.html#go_to_message" not in case.notes
        assert all(len(value) == 16 for value in case.gold_discussion_hashes)


def test_known_present_cases_have_nontrivial_discussion_contracts():
    present = [case for case in golden_cases() if case.expectation == "present"]
    assert len(present) >= 4
    assert all(case.topic_anchors for case in present)
    assert any(case.required_facets for case in present)
