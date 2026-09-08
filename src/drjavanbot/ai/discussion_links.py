from __future__ import annotations

from typing import Sequence
from drjavanbot.search import EvidenceCandidate
from .discussion_types import _HitState, MAX_ANCHOR_MERGE_DISTANCE, MAX_TOPIC_BRIDGE_DISTANCE

def _anchor_group_strength(state: _HitState, group: Sequence[_HitState]) -> float:
    candidate = state.candidate
    if any(_direct_reply_related(candidate, member.candidate) for member in group):
        return 5.0
    message = candidate.message
    if (
        message.reply_to_message_id is not None
        and any(
            member.candidate.message.reply_to_message_id == message.reply_to_message_id
            for member in group
        )
    ):
        return 4.0

    same_page = [
        member.candidate
        for member in group
        if member.candidate.message.source_page == message.source_page
    ]
    if not same_page:
        return 0.0
    orders = [item.message.source_order for item in same_page]
    new_min = min(min(orders), message.source_order)
    new_max = max(max(orders), message.source_order)
    if new_max - new_min > MAX_TOPIC_BRIDGE_DISTANCE:
        return 0.0
    nearest = min(abs(item.message.source_order - message.source_order) for item in same_page)
    if nearest <= MAX_ANCHOR_MERGE_DISTANCE:
        return 2.0 - (nearest / 10.0)
    return 0.0


def _topic_link_strength(facet: EvidenceCandidate, anchor: EvidenceCandidate) -> float:
    if _direct_reply_related(facet, anchor):
        return 4.0
    fmsg, amsg = facet.message, anchor.message
    if (
        fmsg.reply_to_message_id is not None
        and fmsg.reply_to_message_id == amsg.reply_to_message_id
    ):
        return 3.2
    distance = _same_page_distance(facet, anchor)
    if distance <= 2:
        return 3.0
    if distance <= 4:
        return 2.2
    if distance <= MAX_TOPIC_BRIDGE_DISTANCE:
        return 1.3
    return 0.0


def _direct_reply_related(left: EvidenceCandidate, right: EvidenceCandidate) -> bool:
    lmsg, rmsg = left.message, right.message
    return bool(
        (lmsg.reply_to_message_id is not None and lmsg.reply_to_message_id == rmsg.message_id)
        or (rmsg.reply_to_message_id is not None and rmsg.reply_to_message_id == lmsg.message_id)
    )


def _same_page_distance(left: EvidenceCandidate, right: EvidenceCandidate) -> int:
    if left.message.source_page != right.message.source_page:
        return 10_000
    return abs(left.message.source_order - right.message.source_order)
