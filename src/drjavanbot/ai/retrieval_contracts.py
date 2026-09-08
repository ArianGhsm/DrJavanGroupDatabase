from __future__ import annotations

from dataclasses import dataclass
from drjavanbot.search import EvidenceCandidate

# Included in the final-answer cache namespace. Bump whenever ranking,
# discussion/facet semantics or stopping behavior changes in a way that can
# alter which archive evidence reaches reasoning while the SQLite index stays
# byte-identical.
RETRIEVAL_SEMANTICS_VERSION = "discussion-retrieval-v2.1-typed-policy"


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    candidates: tuple[EvidenceCandidate, ...]
    query_runs: int
    families_with_hits: int
    duplicate_queries_skipped: int = 0
    context_hydrated: int = 0
    discussion_windows: int = 0
    conversation_bridges: int = 0
    hit_family_names: tuple[str, ...] = ()
    discussion_count: int = 0
    facet_complete_discussions: int = 0
    topic_anchored_bridges: int = 0
    hydrated_discussions: int = 0
    duplicates_suppressed: int = 0
    ranking_reason_counts: tuple[tuple[str, int], ...] = ()
    quality_state: str = "no_candidates"
    families_executed: int = 0
    topic_anchored_discussions: int = 0
    required_facet_groups_total: int = 0
    max_required_facet_groups_hit: int = 0


__all__ = ["RETRIEVAL_SEMANTICS_VERSION", "RetrievalReport"]
