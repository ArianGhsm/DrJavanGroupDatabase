from datetime import datetime, timezone

from drjavanbot.ai.discussion_facets import _anchor_family_names, _mark_topic_anchors
from drjavanbot.ai.discussion_types import _FamilyHit, _HitState
from drjavanbot.ai.planner import deterministic_fallback_plan
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _state(text: str, family: str = "intersection") -> _HitState:
    record = MessageRecord(
        message_id=1,
        dom_id="message1",
        source_file="messages1.html",
        source_page=1,
        source_order=1,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="2026-01-01",
        author="A",
        author_normalized="a",
        text_raw=text,
        text_normalized=text,
        source_locator="messages1.html#message1",
    )
    candidate = EvidenceCandidate(record, 5.0, (), ("fts_bm25",))
    return _HitState(
        candidate=candidate,
        family_hits={family: _FamilyHit(1, 1.0, 1.0, 5.0, True)},
        matched_terms=set(),
        match_reasons={"fts_bm25"},
    )


def test_partial_or_generic_facet_hit_cannot_manufacture_topic_anchor():
    plan = deterministic_fallback_plan("کدام کیست های اودونتوژنیک رایج تر هستند؟")
    assert "prevalence" in plan.answer_facets
    assert plan.topic_anchor_groups
    anchors = _anchor_family_names(plan)

    noise = _state("این پیشنهاد رایج تر بود")
    _mark_topic_anchors((noise,), plan=plan, anchor_families=anchors)
    assert not noise.topic_anchor

    partial = _state("درباره کیست پیشنهاد شد")
    _mark_topic_anchors((partial,), plan=plan, anchor_families=anchors)
    assert not partial.topic_anchor

    relevant = _state("کیست ادنتوژنیک شایع است")
    _mark_topic_anchors((relevant,), plan=plan, anchor_families=anchors)
    assert relevant.topic_anchor
