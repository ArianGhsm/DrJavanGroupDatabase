from __future__ import annotations

from dataclasses import dataclass

from drjavanbot.search import EvidenceCandidate

RRF_K = 60.0
MAX_TOPIC_BRIDGE_DISTANCE = 8
MAX_ANCHOR_MERGE_DISTANCE = 6
MAX_DISCUSSIONS = 140
MAX_MEMBERS_PER_DISCUSSION = 20

@dataclass(frozen=True, slots=True)
class DiscussionCandidate:
    key: str
    representative: EvidenceCandidate
    members: tuple[EvidenceCandidate, ...]
    family_names: tuple[str, ...]
    anchor_families: tuple[str, ...]
    facet_families: tuple[str, ...]
    required_facet_groups_hit: int
    required_facet_groups_total: int
    reply_edges: int
    proximity_edges: int
    author_count: int
    correction_cues: int
    span: int
    score: float
    ranking_reasons: tuple[str, ...]

    @property
    def topic_anchored(self) -> bool:
        return bool(self.anchor_families)

    @property
    def facet_complete(self) -> bool:
        return (
            self.topic_anchored
            and self.required_facet_groups_total > 0
            and self.required_facet_groups_hit >= self.required_facet_groups_total
        )


@dataclass(slots=True)
class _FamilyHit:
    rank: int
    rrf: float
    local_norm: float
    local_score: float
    qualified: bool


@dataclass(slots=True)
class _HitState:
    candidate: EvidenceCandidate
    family_hits: dict[str, _FamilyHit]
    matched_terms: set[str]
    match_reasons: set[str]
    topic_anchor: bool = False

    @property
    def families(self) -> set[str]:
        return set(self.family_hits)

    @property
    def qualified_families(self) -> set[str]:
        return {name for name, hit in self.family_hits.items() if hit.qualified}

    @property
    def base_score(self) -> float:
        if not self.family_hits:
            return 0.0
        values = tuple(self.family_hits.values())
        best_norm = max(item.local_norm for item in values)
        rrf_sum = min(3.0, sum(item.rrf for item in values))
        best_local = max(item.local_score for item in values)
        score = 2.35 * best_norm + 0.95 * rrf_sum + 0.09 * min(max(best_local, 0.0), 10.0)
        score += 0.20 * min(3, max(0, len(values) - 1))
        if "exact_phrase" in self.match_reasons:
            score += 0.42
        elif "normalized_tokens" in self.match_reasons:
            score += 0.18
        return score
