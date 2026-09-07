from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Sequence

from drjavanbot.domain import MessageRecord


@dataclass(frozen=True, slots=True)
class SearchQuery:
    raw_query: str
    normalized_query: str = ""
    variants: tuple[str, ...] = field(default_factory=tuple)
    author: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    candidate_limit: int = 120
    evidence_limit: int = 40
    context_before: int | None = None
    context_after: int | None = None
    reply_depth: int = 3
    # Direct/legacy search keeps context by default. Semantic multi-query
    # retrieval can defer it until after score fusion to avoid expanding dozens
    # of losing candidates from every query family.
    include_context: bool = True


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    message: MessageRecord
    local_score: float
    matched_terms: tuple[str, ...]
    match_reasons: tuple[str, ...]
    context: tuple[MessageRecord, ...] = field(default_factory=tuple)
    cluster_key: str | None = None
    cluster_size: int = 1
    duplicate_of: int | None = None


class SearchBackend(Protocol):
    def search(self, query: SearchQuery) -> Sequence[EvidenceCandidate]:
        ...

    def get_message(self, message_id: int) -> MessageRecord | None:
        ...

    def get_context(
        self,
        message: MessageRecord,
        *,
        before: int = 2,
        after: int = 3,
        follow_reply: bool = True,
    ) -> Sequence[MessageRecord]:
        ...

    def stats(self) -> dict[str, int | str | float | None]:
        ...
