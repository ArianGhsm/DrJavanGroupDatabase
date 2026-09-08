from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.discussion_facets import _anchor_family_names, _required_family_groups
from drjavanbot.ai.integration_policy import assess_integrated_answerability, should_refine_retrieval
from drjavanbot.ai.models import EvidenceMessage, EvidencePack
from drjavanbot.ai.orchestrator import _cache_key
import drjavanbot.ai.orchestrator as orchestrator_module
from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.query_model import AnswerFacet, EvidencePattern, FamilyPurpose, RetrievalDepth, RetrievalPolicy
from drjavanbot.ai.retrieval_contracts import RetrievalReport
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _record(mid: int, text: str, author: str = "A") -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="گروه دکتر جوان/messages1.html",
        source_page=1,
        source_order=mid,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="01.01.2026 00:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        source_locator=f"گروه دکتر جوان/messages1.html#go_to_message{mid}",
    )


def _candidate(mid: int, text: str, author: str = "A") -> EvidenceCandidate:
    return EvidenceCandidate(
        message=_record(mid, text, author),
        local_score=8.0,
        matched_terms=tuple(text.split()[:2]),
        match_reasons=("exact_phrase",),
    )


def _typed_timing_plan() -> SearchPlan:
    return SearchPlan(
        searchable=True,
        intent="timing_age",
        normalized_intent="timing_age",
        core_concepts=("ارتودنسی",),
        topic_anchors=("ارتودنسی",),
        aliases=("orthodontic",),
        optional_concepts=(),
        entity_types=("procedure",),
        answer_facets=(AnswerFacet.TIMING_AGE,),
        query_families=(
            SearchFamily("subject", ("ارتودنسی",), purpose=FamilyPurpose.TOPIC, anchor=True),
            SearchFamily("age_facet", ("سن", "سالگی"), purpose=FamilyPurpose.FACET),
        ),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=("topic", "timing_age"),
        retrieval_policy=RetrievalPolicy(depth=RetrievalDepth.DEEP, query_budget=12, family_budget=6),
    )


def test_typed_planner_anchor_and_facet_contract_drive_retrieval_helpers():
    plan = _typed_timing_plan()
    anchors = _anchor_family_names(plan)
    groups = _required_family_groups(plan, anchors)
    assert anchors == {"subject"}
    assert groups == ({"age_facet"},)


def test_missing_typed_required_facet_is_not_silently_removed():
    # Construct the consumer-side contract directly. SearchPlan.from_dict is a
    # planner adapter and deliberately restores generic anti-omission families;
    # this regression is specifically about retrieval refusing to shrink the
    # denominator if a required typed facet is nevertheless absent at its boundary.
    source = _typed_timing_plan()
    plan = replace(source, query_families=(source.query_families[0],))
    groups = _required_family_groups(plan, _anchor_family_names(plan))
    assert len(groups) == 1
    assert next(iter(groups[0])).startswith("__missing_required_facet__:")


def test_facet_complete_deep_discussion_stops_redundant_refinement():
    plan = _typed_timing_plan()
    report = RetrievalReport(
        candidates=(_candidate(1, "ارتودنسی سن", "A"), _candidate(2, "ارتودنسی سالگی", "B")),
        query_runs=4,
        families_with_hits=2,
        quality_state="facet_complete_discussion",
        discussion_count=2,
        facet_complete_discussions=1,
        topic_anchored_discussions=2,
        required_facet_groups_total=1,
        max_required_facet_groups_hit=1,
    )
    needs, reason = should_refine_retrieval(plan, report, legacy_needs_refinement=True, legacy_reason="legacy")
    assert not needs and reason == "facet_complete_discussion"


def test_multi_source_policy_retains_one_rescue_for_sparse_single_discussion():
    plan = _typed_timing_plan()
    policy = replace(
        plan.retrieval_policy,
        expected_evidence_pattern=EvidencePattern.MULTI_SOURCE,
    )
    plan = replace(
        plan,
        expected_evidence_pattern=EvidencePattern.MULTI_SOURCE,
        retrieval_policy=policy,
    )
    report = RetrievalReport(
        candidates=(_candidate(1, "ارتودنسی سن", "A"), _candidate(2, "ارتودنسی سن", "B")),
        query_runs=4,
        families_with_hits=2,
        quality_state="facet_complete_discussion",
        discussion_count=1,
        facet_complete_discussions=1,
        topic_anchored_discussions=1,
        required_facet_groups_total=1,
        max_required_facet_groups_hit=1,
    )
    needs, reason = should_refine_retrieval(plan, report, legacy_needs_refinement=False, legacy_reason="complete")
    assert needs and reason == "multi_source_diversity_rescue"


def test_answerability_uses_discussion_colocation_not_global_family_names():
    plan = _typed_timing_plan()
    pack = EvidencePack(
        question="سن ارتودنسی؟",
        normalized_question="سن ارتودنسی",
        budget_name="test",
        estimated_tokens=40,
        messages=(
            EvidenceMessage(
                source_ref="m1",
                message_id=1,
                author="A",
                datetime=None,
                source_file="messages1.html",
                text="ارتودنسی در این بحث مطرح شد و سن هم ذکر شد",
                role="evidence",
            ),
        ),
    )
    report = RetrievalReport(
        candidates=(_candidate(1, "ارتودنسی سن"),),
        query_runs=2,
        families_with_hits=2,
        quality_state="facet_complete_discussion",
        discussion_count=1,
        facet_complete_discussions=1,
        topic_anchored_discussions=1,
        required_facet_groups_total=1,
        max_required_facet_groups_hit=1,
    )
    assessment = assess_integrated_answerability(pack, plan, report)
    assert assessment.answerable
    assert assessment.facet_coverage == 1.0
    assert assessment.reason_code == "fragmented_but_answerable"


def test_final_answer_cache_namespace_includes_retrieval_semantics(monkeypatch):
    config = AIConfig()
    before = _cache_key("کامپوزیت", "idx", config)
    monkeypatch.setattr(orchestrator_module, "RETRIEVAL_SEMANTICS_VERSION", "changed-retrieval-semantics")
    after = _cache_key("کامپوزیت", "idx", config)
    assert before != after
