from datetime import datetime, timezone

from drjavanbot.ai.config import AIConfig
from drjavanbot.ai.evidence import build_evidence_pack
from drjavanbot.domain import MessageRecord
from drjavanbot.search import EvidenceCandidate


def _record(mid: int, author: str, text: str, *, reply: int | None = None) -> MessageRecord:
    return MessageRecord(
        message_id=mid,
        dom_id=f"message{mid}",
        source_file="گروه دکتر جوان/messages20.html",
        source_page=20,
        source_order=mid,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_raw="01.01.2026 12:00:00 UTC+03:30",
        author=author,
        author_normalized=author.casefold(),
        text_raw=text,
        text_normalized=text.casefold(),
        reply_to_message_id=reply,
        source_locator=f"گروه دکتر جوان/messages20.html#go_to_message{mid}",
    )


def _candidate(mid: int, *, contexts=(), reply: int | None = None) -> EvidenceCandidate:
    return EvidenceCandidate(
        message=_record(mid, f"Author {mid}", f"کامپوزیت evidence {mid}", reply=reply),
        local_score=10.0 - mid / 100.0,
        matched_terms=("کامپوزیت",),
        match_reasons=("exact_phrase",),
        context=tuple(contexts),
    )


def test_context_heavy_first_thread_cannot_starve_diverse_primary_evidence():
    first_context = tuple(_record(100 + i, "Context A", f"context {i}") for i in range(8))
    candidates = [_candidate(1, contexts=first_context)]
    candidates.extend(_candidate(i) for i in range(2, 12))

    config = AIConfig(simple_messages=8, hard_messages=8, simple_evidence_tokens=5000, hard_evidence_tokens=5000)
    pack = build_evidence_pack("کامپوزیت", candidates, config)

    primaries = [item for item in pack.messages if item.role == "evidence"]
    contexts = [item for item in pack.messages if item.role != "evidence"]
    assert len(pack.messages) <= 8
    assert len(primaries) >= 6
    assert len({item.author for item in primaries if item.author}) >= 6
    assert len(contexts) <= 2


def test_direct_reply_parent_is_prioritized_over_neighborhood_context():
    parent = _record(50, "Parent", "کامپوزیت ProductQ")
    before = _record(49, "Other", "پیام اطراف")
    after = _record(52, "Other 2", "پیام بعدی")
    reply = EvidenceCandidate(
        message=_record(51, "Reply", "من اینو خیلی دوست داشتم", reply=50),
        local_score=8.0,
        matched_terms=("productq",),
        match_reasons=("reply_context",),
        context=(parent, before, after),
    )
    other_candidates = tuple(_candidate(i) for i in range(60, 66))
    config = AIConfig(simple_messages=6, hard_messages=6, simple_evidence_tokens=4000, hard_evidence_tokens=4000)

    pack = build_evidence_pack("ProductQ", (reply, *other_candidates), config)
    by_id = {item.message_id: item for item in pack.messages}

    assert 51 in by_id
    assert 50 in by_id
    assert by_id[50].role == "reply_context"
    assert by_id[50].parent_source_ref == by_id[51].source_ref
