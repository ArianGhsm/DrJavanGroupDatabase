from datetime import datetime, timezone

from drjavanbot.intelligence.answerability import assess_requested_fact_coverage
from drjavanbot.intelligence.models import EvidenceItem, SourceType
from drjavanbot.intelligence.routing import route_sources
from drjavanbot.intelligence.understanding import understand_question


def _evidence(text, source=SourceType.SCIENTIFIC, timestamp="2026-09-01T00:00:00+00:00"):
    return EvidenceItem(
        evidence_id="e1",
        source_type=source,
        source_name=str(source),
        source_ref=f"{source}:1",
        text=text,
        timestamp=timestamp,
        retrieval_score=1.0,
        trust_tier="test",
    )


def test_topic_only_odontogenic_cyst_evidence_is_not_prevalence_answerable():
    u = understand_question("کدام کیست های اودونتوژنیک رایج تر هستند؟")
    route = route_sources(u)
    coverage = assess_requested_fact_coverage(
        u,
        route,
        (_evidence("odontogenic cysts are jaw lesions"),),
        now=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert coverage.topic_present
    assert not coverage.requested_fact_supported
    assert not coverage.answerable
    assert coverage.reason_code == "requested_fact_missing"


def test_prevalence_signal_must_cooccur_with_topic_and_required_source():
    u = understand_question("most common odontogenic cyst?")
    route = route_sources(u)
    evidence = _evidence("Among odontogenic cysts, this category is the most common and has the highest prevalence.")
    coverage = assess_requested_fact_coverage(u, route, (evidence,))
    assert coverage.requested_fact_supported
    assert coverage.source_requirement_satisfied
    assert coverage.answerable
    assert coverage.requested_facets[0].signal_code == "facet_semantic_signal"


def test_salary_requires_numeric_compensation_context_freshness_and_current_source():
    u = understand_question("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟")
    route = route_sources(u)
    old_archive = _evidence(
        "حقوق دندانپزشک تازه کار مطرح شد",
        source=SourceType.ARCHIVE,
        timestamp="2024-01-01T00:00:00+00:00",
    )
    stale = assess_requested_fact_coverage(u, route, (old_archive,), now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert not stale.requested_fact_supported
    assert not stale.source_requirement_satisfied
    assert not stale.freshness_satisfied
    assert not stale.answerable

    current = _evidence(
        "حقوق دندانپزشک تازه فارغ التحصیل 100 میلیون تومان در ماه گزارش شده است",
        source=SourceType.CURRENT_WEB,
        timestamp="2026-09-01T00:00:00+00:00",
    )
    covered = assess_requested_fact_coverage(u, route, (current,), now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert covered.requested_fact_supported
    assert covered.freshness_satisfied
    assert covered.source_requirement_satisfied
    assert covered.answerable


def test_single_source_contrast_language_does_not_manufacture_cross_source_conflict():
    from drjavanbot.intelligence.answerability import assess_requested_fact_coverage
    from drjavanbot.intelligence.models import EvidenceItem, SourceType
    u=understand_question("حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟")
    route=route_sources(u)
    e=EvidenceItem(evidence_id="web:1",source_type=SourceType.CURRENT_WEB,source_name="current",source_ref="https://example.com/x",text="دندانپزشک تازه کار ۴۰ تا ۹۰ میلیون تومان درآمد دارد اما بسته به قرارداد متغیر است",timestamp="2026-09-01T00:00:00+00:00",metadata={"current_year_signal":True},trust_tier="current_web")
    c=assess_requested_fact_coverage(u,route,(e,))
    assert not c.conflicts


def test_unknown_identifier_must_cooccur_with_evidence_topic_before_synthesis():
    u=understand_question("درمان ضایعه خیالی zqv-99 چیه؟")
    route=route_sources(u)
    unrelated=EvidenceItem(evidence_id="p",source_type=SourceType.SCIENTIFIC,source_name="PubMed",source_ref="PMID:1",text="Treatment management of an unrelated oral lesion",trust_tier="peer_reviewed_primary")
    c=assess_requested_fact_coverage(u,route,(unrelated,))
    assert not c.topic_present
    assert not c.answerable
    assert c.reason_code == "topic_missing"
