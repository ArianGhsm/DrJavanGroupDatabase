from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .facets import evidence_signal
from .models import EvidenceItem, RetrievalRequest


class FacetAwareReranker:
    """Deterministic Stage-1 reranker emphasizing requested-fact evidence."""

    def rerank(self, request: RetrievalRequest, items: Iterable[EvidenceItem]) -> tuple[EvidenceItem, ...]:
        scored: list[tuple[float, EvidenceItem]] = []
        values = tuple(items)
        max_retrieval = max((float(item.retrieval_score or 0.0) for item in values), default=1.0) or 1.0
        for item in values:
            text = "\n".join(value for value in (item.title, item.text, item.context) if value)
            facet_scores = [evidence_signal(text, facet)[1] for facet in request.facets]
            requested_fact = sum(facet_scores) / len(facet_scores) if facet_scores else 0.5
            local = max(0.0, float(item.retrieval_score or 0.0)) / max_retrieval
            topic_bonus = 1.0 if any(
                reason in {"discussion_topic_anchor", "anchor_family_hit", "discussion_exact_anchor"}
                for reason in item.metadata.get("match_reasons", ())
            ) else 0.0
            final = 0.50 * local + 0.40 * requested_fact + 0.10 * topic_bonus
            scored.append((final, replace(item, semantic_score=round(final, 6))))
        scored.sort(key=lambda pair: (-pair[0], -(pair[1].retrieval_score or 0.0), pair[1].evidence_id))
        return tuple(item for _score, item in scored)


__all__ = ["FacetAwareReranker"]
