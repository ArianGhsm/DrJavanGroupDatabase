from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from drjavanbot.ai.eval.runner import evaluate_case
from drjavanbot.ai.eval.schema import GoldenCase
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _record(mid: int, page: int, order: int, text: str, author: str = "A") -> MessageRecord:
    return MessageRecord(
        message_id=mid, dom_id=f"message{mid}", source_file=f"synthetic/messages{page}.html",
        source_page=page, source_order=order, datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="synthetic", author=author, author_normalized=author.casefold(),
        text_raw=text, text_normalized=text.casefold(), source_locator=f"synthetic/p{page}#{mid}",
    )


def _candidate(mid: int, page: int, order: int, text: str, author: str = "A") -> EvidenceCandidate:
    return EvidenceCandidate(_record(mid, page, order, text, author), 8.0, (), ("exact_phrase",))


def _position_hash(page: int, order: int) -> str:
    raw_key = f"p{page}:b{order // 12}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


class _Backend:
    def __init__(self, mapping, context=()):
        self.mapping = mapping
        self.context = tuple(context)
    def search(self, query):
        q = query.raw_query.casefold()
        for key, values in self.mapping.items():
            if key in q:
                return tuple(values)
        return ()
    def get_context(self, message, **kwargs):
        return self.context
    def get_message(self, message_id):
        return None
    def stats(self):
        return {"index_version": "synthetic"}


def _case() -> GoldenCase:
    return GoldenCase(
        case_id="colocation", category="age_timing_population", question="ortho age",
        expectation="present", query_families=(("topic", ("ortho",)), ("age", ("age",))),
        topic_anchors=("ortho",), required_facets=(("age", "year"),), known_answerable=True,
    )


def test_global_topic_plus_facet_in_different_discussions_does_not_pass():
    backend = _Backend({
        "ortho": (_candidate(1, 1, 10, "ortho"),),
        "age": (_candidate(2, 2, 10, "age 8"),),
    })
    report = evaluate_case(backend, _case(), top_k=8)
    assert not report.gate_passed
    assert report.reason_code == "facet_not_colocated"
    assert report.required_facets_colocated == 0
    assert report.discussion_recall_at_k == 0.0


def test_unrelated_nearby_candidates_do_not_form_a_fake_discussion_bridge():
    backend = _Backend({
        "ortho": (_candidate(1, 1, 11, "ortho"),),
        "age": (_candidate(2, 1, 25, "age 8"),),
    })
    report = evaluate_case(backend, _case(), top_k=8)
    assert not report.gate_passed
    assert report.reason_code == "facet_not_colocated"
    assert report.facet_colocation_complete is False


def test_context_only_topic_and_direct_facet_in_same_bundle_is_recovered():
    facet = _candidate(1, 1, 10, "age 8", "A")
    parent = _record(9, 1, 9, "ortho", "B")
    backend = _Backend({"ortho": (facet,), "age": (facet,)}, context=(parent,))
    report = evaluate_case(backend, _case(), top_k=8)
    assert report.gate_passed
    assert report.facet_colocation_complete is True
    assert report.context_only_recovery_rate == 1.0
    assert report.author_diversity == 1
    assert report.relevant_discussion_hashes


def test_frozen_gold_matches_any_admitted_member_of_same_discussion_bundle():
    # The v2 retriever may choose a different representative than BASE while
    # retaining the BASE representative as an admitted discussion member/context.
    # Quality Lab must compare the frozen gold against the discussion bundle, not
    # only against the newly chosen representative position.
    representative = _candidate(1, 4, 37, "age 8", "A")
    base_member = _record(9, 4, 11, "ortho", "B")
    case = GoldenCase(
        case_id="gold_bundle", category="age_timing_population", question="ortho age",
        expectation="present", query_families=(("topic", ("ortho",)), ("age", ("age",))),
        topic_anchors=("ortho",), required_facets=(("age", "year"),), known_answerable=True,
        gold_discussion_hashes=(_position_hash(4, 11),),
    )
    report = evaluate_case(
        _Backend({"ortho": (representative,), "age": (representative,)}, context=(base_member,)),
        case,
        top_k=8,
    )
    assert report.gate_passed
    assert report.discussion_recall_at_k == 1.0
    assert _position_hash(4, 11) in report.relevant_discussion_hashes


def test_absent_case_allows_irrelevant_fallback_candidates_but_not_supported_evidence():
    case = GoldenCase(
        case_id="absent", category="no_evidence_sentinel", question="unseen",
        expectation="absent", query_families=(("topic", ("unseen",)),), topic_anchors=("unseen",),
    )
    report = evaluate_case(_Backend({"unseen": (_candidate(1, 1, 10, "totally unrelated"),)}), case, top_k=5)
    assert report.gate_passed
    assert report.reason_code == "absent_irrelevant_only"
    assert report.supported_answer_observed is False
    assert report.irrelevant_candidate_rate == 1.0


def test_absent_case_fails_when_relevant_evidence_is_present():
    case = GoldenCase(
        case_id="absent", category="no_evidence_sentinel", question="unseen",
        expectation="absent", query_families=(("topic", ("unseen",)),), topic_anchors=("unseen",),
    )
    report = evaluate_case(_Backend({"unseen": (_candidate(1, 1, 10, "unseen is here"),)}), case, top_k=5)
    assert not report.gate_passed
    assert report.reason_code == "unexpected_relevant_evidence"
    assert report.supported_answer_observed is True


def test_duplicate_query_family_is_measured_before_scheduler_dedup():
    case = GoldenCase(
        case_id="dupe", category="duplicate_query_family", question="x", expectation="observe",
        query_families=(("a", ("same",)), ("b", ("same",))), topic_anchors=("same",),
    )
    report = evaluate_case(_Backend({"same": (_candidate(1, 1, 10, "same"),)}), case, top_k=5)
    assert report.duplicate_family_queries == 1
    assert report.query_count == 1


def test_one_author_echo_and_thread_concentration_are_visible_metrics():
    values = tuple(_candidate(i, 1, 10 + i, "topic", "same") for i in range(1, 5))
    case = GoldenCase(
        case_id="echo", category="author_diversity", question="topic", expectation="observe",
        query_families=(("topic", ("topic",)),), topic_anchors=("topic",),
    )
    report = evaluate_case(_Backend({"topic": values}), case, top_k=4)
    assert report.author_diversity == 1
    assert report.duplicate_thread_concentration >= 0.5
