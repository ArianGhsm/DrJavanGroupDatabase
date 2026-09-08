from __future__ import annotations

from datetime import datetime, timezone

from drjavanbot.ai.planner import SearchFamily, SearchPlan
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _candidate(mid: int, order: int, text: str, author: str) -> EvidenceCandidate:
    record = MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="گروه دکتر جوان/messages90.html",
        source_page=90,
        source_order=order,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="01.01.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        source_locator=f"گروه دکتر جوان/messages90.html#go_to_message{mid}",
    )
    return EvidenceCandidate(
        message=record,
        local_score=7.0,
        matched_terms=tuple(text.casefold().split()[:2]),
        match_reasons=("normalized_tokens",),
    )


class Backend:
    def __init__(self) -> None:
        self.topic = _candidate(1, 100, "موضوع اصلی", "A")
        self.correction = _candidate(2, 103, "نه اشتباه؛ سن متفاوت است", "B")

    def search_many(self, queries):
        out = []
        for query in queries:
            if "موضوع اصلی" in query.raw_query:
                out.append((self.topic,))
            elif "سن" in query.raw_query:
                out.append((self.correction,))
            else:
                out.append(())
        return tuple(out)

    def search(self, query):
        return self.search_many((query,))[0]

    def get_context(self, message, *, before=2, after=3, follow_reply=True):
        return ()

    def get_message(self, message_id):
        return None

    def stats(self):
        return {"messages": 2}


def test_correction_cues_are_bounded_discussion_metadata_without_raw_text_telemetry():
    plan = SearchPlan(
        searchable=True,
        intent="timing_age",
        core_concepts=("موضوع اصلی",),
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=(
            SearchFamily("topic", ("موضوع اصلی",)),
            SearchFamily("timing", ("سن",)),
        ),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=("topic", "timing_age"),
    )

    report = retrieve_with_plan(Backend(), plan, evidence_limit=12)

    assert report.candidates
    assert any(
        reason.startswith("discussion_correction_cues:")
        for reason in report.candidates[0].match_reasons
    )
    telemetry = " ".join(reason for reason, _count in report.ranking_reason_counts)
    assert "اشتباه" not in telemetry
    assert "متفاوت" not in telemetry
    assert len(report.ranking_reason_counts) <= 24
