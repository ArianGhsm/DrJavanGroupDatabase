from __future__ import annotations

from collections import defaultdict
from typing import Sequence
from .discussion_types import DiscussionCandidate, _HitState, MAX_DISCUSSIONS, MAX_MEMBERS_PER_DISCUSSION
from .discussion_links import _anchor_group_strength, _topic_link_strength
from .discussion_score import _make_discussion

def _build_discussions(
    states: Sequence[_HitState],
    *,
    anchor_families: set[str],
    required_groups: Sequence[set[str]],
) -> tuple[tuple[DiscussionCandidate, ...], int]:
    values = tuple(states)
    if not values:
        return (), 0

    anchors = [state for state in values if state.topic_anchor]
    facets = [state for state in values if not state.topic_anchor]

    anchor_groups: list[list[_HitState]] = []
    if anchors:
        # Greedy anchor-centred grouping deliberately avoids transitive chaining:
        # A~B and B~C must not create an arbitrarily wide A..C window.
        for state in sorted(
            anchors,
            key=lambda item: (
                item.candidate.message.source_page,
                item.candidate.message.source_order,
                -item.base_score,
            ),
        ):
            best_group: int | None = None
            best_strength = 0.0
            for group_index, group in enumerate(anchor_groups):
                strength = _anchor_group_strength(state, group)
                if strength > best_strength:
                    best_strength = strength
                    best_group = group_index
            if best_group is None or best_strength <= 0.0:
                anchor_groups.append([state])
            else:
                anchor_groups[best_group].append(state)

    assigned_facets: dict[int, list[_HitState]] = defaultdict(list)
    unassigned_facets: list[_HitState] = []
    bridge_count = 0
    for facet in facets:
        best_group: int | None = None
        best_strength = 0.0
        for group_index, group in enumerate(anchor_groups):
            strength = max(
                (_topic_link_strength(facet.candidate, anchor.candidate) for anchor in group),
                default=0.0,
            )
            if strength > best_strength:
                best_strength = strength
                best_group = group_index
        if best_group is not None and best_strength > 0.0:
            assigned_facets[best_group].append(facet)
            bridge_count += 1
        else:
            unassigned_facets.append(facet)

    discussions: list[DiscussionCandidate] = []
    for group_index, anchor_group in enumerate(anchor_groups):
        members = tuple((anchor_group + assigned_facets.get(group_index, []))[:MAX_MEMBERS_PER_DISCUSSION])
        discussions.append(_make_discussion(
            members,
            anchor_families=anchor_families,
            required_groups=required_groups,
            anchored=True,
        ))

    for state in unassigned_facets[:80]:
        discussions.append(_make_discussion(
            (state,),
            anchor_families=anchor_families,
            required_groups=required_groups,
            anchored=False,
            anchor_exists=bool(anchor_groups),
        ))

    if not anchors and not anchor_families:
        discussions = [
            _make_discussion(
                (state,),
                anchor_families=set(),
                required_groups=required_groups,
                anchored=True,
            )
            for state in values[:MAX_DISCUSSIONS]
        ]
    return tuple(discussions), bridge_count
