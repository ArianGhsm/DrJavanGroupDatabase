from __future__ import annotations

from datetime import datetime, timezone

from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.retrieval import assess_planned_retrieval, retrieve_with_plan
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _record(
    mid: int,
    page: int,
    order: int,
    text: str,
    author: str,
    *,
    reply: int | None = None,
) -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file=f"گروه دکتر جوان/messages{page}.html",
        source_page=page,
        source_order=order,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="01.01.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        reply_to_message_id=reply,
        source_locator=f"گروه دکتر جوان/messages{page}.html#go_to_message{mid}",
    )


def _candidate(
    mid: int,
    page: int,
    order: int,
    text: str,
    author: str,
    *,
    reply: int | None = None,
    score: float = 7.0,
    reason: str = "normalized_tokens",
) -> EvidenceCandidate:
    return EvidenceCandidate(
        message=_record(mid, page, order, text, author, reply=reply),
        local_score=score,
        matched_terms=tuple(text.casefold().split()[:2]),
        match_reasons=(reason,),
    )


def _timing_plan() -> SearchPlan:
    return SearchPlan(
        searchable=True,
        intent="timing_age",
        core_concepts=("موضوع اصلی",),
        aliases=(),
        optional_concepts=(),
        entity_types=("procedure",),
        query_families=(
            SearchFamily("topic", ("موضوع اصلی",)),
            SearchFamily("timing", ("سن", "سالگی")),
            SearchFamily("population", ("کودک",)),
        ),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=("topic", "timing_age", "pediatric_population"),
    )


class RoutingBackend:
    def __init__(self, routes: dict[str, tuple[EvidenceCandidate, ...]], context=()):
        self.routes = routes
        self.context = tuple(context)
        self.queries = []
        self.context_calls: list[tuple[int | None, int, int]] = []

    def search_many(self, queries):
        self.queries.extend(queries)
        output = []
        for query in queries:
            normalized = query.raw_query.casefold()
            matched = ()
            for key, values in self.routes.items():
                if key.casefold() in normalized:
                    matched = values
                    break
            output.append(matched)
        return tuple(output)

    def search(self, query):
        return self.search_many((query,))[0]

    def get_context(self, message, *, before=2, after=3, follow_reply=True):
        self.context_calls.append((message.message_id, before, after))
        return self.context

    def get_message(self, message_id):
        return None

    def stats(self):
        return {"messages": 100}


def test_required_facets_do_not_colocate_across_unrelated_windows():
    topic = _candidate(10, 3, 100, "موضوع اصلی", "A")
    timing = _candidate(11, 3, 120, "سن شروع", "B")
    population = _candidate(12, 3, 125, "کودک", "C")
    backend = RoutingBackend({
        "موضوع اصلی": (topic,),
        "سن": (timing,),
        "سالگی": (timing,),
        "کودک": (population,),
    })

    report = retrieve_with_plan(backend, _timing_plan(), evidence_limit=24)

    assert report.candidates
    assert report.facet_complete_discussions == 0
    assert report.topic_anchored_bridges == 0
    assert report.quality_state == "only_topical_facet_missing"
    assert all("discussion_facet_complete" not in item.match_reasons for item in report.candidates)
    needs_refinement, reason = assess_planned_retrieval(report)
    assert needs_refinement and reason == "required_facet_missing"


def test_short_reply_cross_page_is_attached_by_reply_graph_not_page_distance():
    topic = _candidate(20, 7, 499, "موضوع اصلی برای کودک", "A")
    timing_reply = _candidate(21, 8, 2, "حدود X سالگی", "B", reply=20)
    backend = RoutingBackend({
        "موضوع اصلی": (topic,),
        "سن": (timing_reply,),
        "سالگی": (timing_reply,),
        "کودک": (topic,),
    })

    report = retrieve_with_plan(backend, _timing_plan(), evidence_limit=24)

    assert report.topic_anchored_bridges >= 1
    assert report.facet_complete_discussions >= 1
    assert report.quality_state == "facet_complete_discussion"
    assert report.candidates[0].message.message_id == 20
    assert any(item.message_id == 21 for item in report.candidates[0].context)
    assert "discussion_reply_edge" in report.candidates[0].match_reasons


def test_generic_facet_pollution_is_suppressed_when_topic_anchor_is_absent():
    noisy = tuple(
        _candidate(100 + index, 20 + index, 10, "سن سالگی", f"A{index}", score=9.0)
        for index in range(12)
    )
    plan = SearchPlan(
        searchable=True,
        intent="timing_age",
        core_concepts=("topic-sentinel-zzqv",),
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=(
            SearchFamily("topic", ("topic-sentinel-zzqv",)),
            SearchFamily("timing", ("سن", "سالگی")),
        ),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=("topic", "timing_age"),
    )
    backend = RoutingBackend({"سن": noisy, "سالگی": noisy})

    report = retrieve_with_plan(backend, plan, evidence_limit=24)

    assert report.families_with_hits == 1
    assert report.hit_family_names == ("timing",)
    assert report.candidates == ()
    assert report.quality_state == "no_candidates"


def test_latin_compound_query_adds_bounded_compact_surface_variant():
    product = _candidate(200, 30, 1, "emax material", "A", reason="exact_phrase")
    backend = RoutingBackend({"e max": (product,)})
    plan = SearchPlan(
        searchable=True,
        intent="direct_product_lookup",
        core_concepts=("e max",),
        aliases=(),
        optional_concepts=(),
        entity_types=("product",),
        query_families=(SearchFamily("product", ("e.max",)),),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
    )

    report = retrieve_with_plan(backend, plan)

    assert report.candidates
    assert backend.queries[0].raw_query == "e max"
    assert backend.queries[0].variants == ("emax",)
    assert report.quality_state == "strong_direct_answer_candidate"


def test_hydration_is_capped_to_top_discussions():
    topics = tuple(
        _candidate(300 + index, 50 + index, 10, "موضوع اصلی", f"A{index}")
        for index in range(30)
    )
    backend = RoutingBackend({"موضوع اصلی": topics})
    plan = SearchPlan(
        searchable=True,
        intent="archive_lookup",
        core_concepts=("موضوع اصلی",),
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=(SearchFamily("topic", ("موضوع اصلی",)),),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
    )

    report = retrieve_with_plan(backend, plan, evidence_limit=56)

    assert report.discussion_count == 30
    assert len(backend.context_calls) == 12
    assert len(report.candidates) == 30
    assert report.context_hydrated == 0
    assert report.hydrated_discussions == 0


def test_partial_or_hit_does_not_fake_compound_facet_coverage():
    topic = _candidate(600, 80, 100, "کامپوزیت", "A", reason="exact_phrase")
    partial_experience = _candidate(601, 80, 103, "کامپوزیت", "B", reason="fts_bm25")
    backend = RoutingBackend({
        "تجربه": (partial_experience,),
        "کامپوزیت": (topic,),
    })
    plan = SearchPlan(
        searchable=True,
        intent="recommendation_comparison",
        core_concepts=("کامپوزیت",),
        aliases=(),
        optional_concepts=(),
        entity_types=("product",),
        query_families=(
            SearchFamily("topic", ("کامپوزیت",)),
            SearchFamily("experience", ("کامپوزیت تجربه",)),
        ),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=("topic", "recommendation"),
    )

    report = retrieve_with_plan(backend, plan, evidence_limit=24)

    assert report.candidates
    assert report.required_facet_groups_total == 1
    assert report.max_required_facet_groups_hit == 0
    assert report.facet_complete_discussions == 0
    assert report.quality_state == "only_topical_facet_missing"


def test_ranking_telemetry_contains_only_bounded_reason_counts():
    topic = _candidate(500, 70, 100, "موضوع اصلی", "A", reason="exact_phrase")
    timing = _candidate(501, 70, 103, "سن", "B")
    population = _candidate(502, 70, 105, "کودک", "C")
    backend = RoutingBackend({
        "موضوع اصلی": (topic,),
        "سن": (timing,),
        "سالگی": (timing,),
        "کودک": (population,),
    })

    report = retrieve_with_plan(backend, _timing_plan(), evidence_limit=24)

    assert report.ranking_reason_counts
    assert len(report.ranking_reason_counts) <= 24
    telemetry = " ".join(name for name, _ in report.ranking_reason_counts)
    assert "موضوع اصلی" not in telemetry
    assert all(count > 0 for _, count in report.ranking_reason_counts)
