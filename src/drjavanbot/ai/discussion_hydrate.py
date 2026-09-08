from __future__ import annotations

from dataclasses import replace
from typing import Sequence
from drjavanbot.search import SearchBackend
from .discussion_types import DiscussionCandidate
from .discussion_features import _context_dependent


def _discussion_hydration_window(discussion: DiscussionCandidate) -> tuple[int, int, bool]:
    if discussion.facet_complete:
        return 5, 7, True
    if discussion.topic_anchored and len(discussion.family_names) >= 3:
        return 5, 6, True
    if discussion.topic_anchored and len(discussion.members) >= 2:
        return 4, 5, True
    if discussion.reply_edges:
        return 4, 4, True
    if _context_dependent(discussion.representative):
        return 3, 4, True
    if discussion.topic_anchored:
        return 2, 3, False
    return 1, 2, False

def _hydrate_top_discussions(
    backend: SearchBackend,
    discussions: Sequence[DiscussionCandidate],
    *,
    reply_context: bool,
    reply_depth: int,
    limit: int,
) -> tuple[tuple[DiscussionCandidate, ...], int, int]:
    values = tuple(discussions)
    if not values or not reply_context:
        return values, 0, 0

    bounded_limit = min(max(0, int(limit)), len(values), 16)
    requests: list[tuple[object, int, int, bool, int]] = []
    windows: list[tuple[int, int, bool]] = []
    for discussion in values[:bounded_limit]:
        before, after, expanded = _discussion_hydration_window(discussion)
        windows.append((before, after, expanded))
        requests.append((discussion.representative.message, before, after, True, max(0, min(reply_depth, 6))))

    fetched: list[tuple] = []
    batch_context = getattr(backend, "get_context_many", None)
    if callable(batch_context):
        try:
            result = tuple(tuple(items) for items in batch_context(tuple(requests)))
            if len(result) == len(requests):
                fetched = list(result)
        except Exception:
            fetched = []

    if len(fetched) != len(requests):
        fetched = []
        for message, before, after, follow_reply, _depth in requests:
            try:
                fetched.append(tuple(backend.get_context(
                    message,
                    before=before,
                    after=after,
                    follow_reply=follow_reply,
                )))
            except Exception:
                fetched.append(())

    output: list[DiscussionCandidate] = []
    hydrated_count = 0
    discussion_windows = 0
    for index, discussion in enumerate(values):
        if index >= bounded_limit:
            packed = _merge_discussion_context(discussion, ())
            if packed:
                output.append(_with_discussion_context(discussion, packed, hydrated=False, expanded=False))
            else:
                output.append(discussion)
            continue

        backend_context = fetched[index]
        packed = _merge_discussion_context(discussion, backend_context)
        expanded = windows[index][2]
        if backend_context:
            hydrated_count += 1
        if expanded and packed:
            discussion_windows += 1
        if packed:
            output.append(_with_discussion_context(
                discussion,
                packed,
                hydrated=bool(backend_context),
                expanded=expanded,
            ))
        else:
            output.append(discussion)
    return tuple(output), hydrated_count, discussion_windows


def _with_discussion_context(
    discussion: DiscussionCandidate,
    context: Sequence,
    *,
    hydrated: bool,
    expanded: bool,
) -> DiscussionCandidate:
    reasons = set(discussion.representative.match_reasons)
    reasons.add("discussion_bundle")
    score = discussion.score
    if context:
        reasons.add("context_available")
        score += 0.12
    if expanded:
        reasons.add("discussion_window")
        score += 0.08
    if hydrated:
        reasons.add("adaptive_context_hydration")
    representative = replace(
        discussion.representative,
        local_score=round(score, 6),
        match_reasons=tuple(sorted(reasons)),
        context=tuple(context),
    )
    return replace(discussion, representative=representative, score=round(score, 6))


def _merge_discussion_context(
    discussion: DiscussionCandidate,
    backend_context: Sequence,
) -> tuple:
    anchor = discussion.representative.message
    records = [
        member.message
        for member in discussion.members
        if member.message.source_locator != anchor.source_locator
    ]
    records.extend(discussion.representative.context)
    records.extend(backend_context)
    dedup: dict[str, object] = {}
    for record in records:
        locator = getattr(record, "source_locator", "")
        if not locator or locator == anchor.source_locator:
            continue
        dedup.setdefault(locator, record)
    ordered = sorted(
        dedup.values(),
        key=lambda record: _context_priority_key(record, anchor),
    )
    return tuple(ordered[:20])


def _context_priority_key(record, anchor) -> tuple[int, int, int, int]:
    if (
        getattr(record, "message_id", None) == anchor.reply_to_message_id
        or getattr(record, "reply_to_message_id", None) == anchor.message_id
    ):
        relation = 0
    elif getattr(record, "source_page", -1) == anchor.source_page:
        relation = 1
    else:
        relation = 2
    if getattr(record, "source_page", -1) == anchor.source_page:
        distance = abs(getattr(record, "source_order", 0) - anchor.source_order)
    else:
        distance = (
            abs(getattr(record, "source_page", 0) - anchor.source_page) * 10_000
            + abs(getattr(record, "source_order", 0) - anchor.source_order)
        )
    return (
        relation,
        distance,
        getattr(record, "source_page", 0),
        getattr(record, "source_order", 0),
    )
