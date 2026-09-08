from __future__ import annotations

from collections import OrderedDict
import time

from drjavanbot.ai.query_model import EvidencePattern, FamilyPurpose, SearchFamily, SearchPlan
from drjavanbot.ai.retrieval import retrieve_with_plan
from drjavanbot.ai.search_policy import derive_retrieval_policy
from drjavanbot.search import SQLiteSearchBackend
from .models import EvidenceItem, RetrievalRequest, RetrievalResult, SourceType


class ArchiveRetrievalProvider:
    source_type = SourceType.ARCHIVE

    def __init__(self, backend: SQLiteSearchBackend) -> None:
        self.backend = backend

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.source_type != SourceType.ARCHIVE:
            raise ValueError("archive provider received non-archive request")
        plan = archive_plan_from_request(request)
        started = time.perf_counter()
        report = retrieve_with_plan(self.backend, plan, evidence_limit=max(12, min(request.top_k, 60)))
        latency = (time.perf_counter() - started) * 1000.0
        items = tuple(_evidence_item(candidate, rank) for rank, candidate in enumerate(report.candidates[:request.top_k], start=1))
        return RetrievalResult(
            source_type=SourceType.ARCHIVE,
            items=items,
            query_count=report.query_runs,
            latency_ms=latency,
        )


def archive_plan_from_request(request: RetrievalRequest) -> SearchPlan:
    grouped: OrderedDict[str, list] = OrderedDict()
    for query in request.queries:
        grouped.setdefault(query.family, []).append(query)

    families: list[SearchFamily] = []
    mandatory_groups: list[tuple[str, ...]] = []
    topic_anchors: list[str] = []
    for name, values in grouped.items():
        first = values[0]
        queries = tuple(item.text for item in values[:4])
        purpose = _family_purpose(first.purpose)
        families.append(SearchFamily(
            name=name,
            queries=queries,
            purpose=purpose,
            priority=max(item.priority for item in values),
            anchor=any(item.anchor for item in values),
        ))
        mandatory = tuple(item.text for item in values if item.anchor and item.mandatory)
        if mandatory:
            mandatory_groups.append(mandatory)
            topic_anchors.append(mandatory[0])

    searchable = bool(families)
    policy = derive_retrieval_policy(
        request.normalized_question,
        facets=request.facets,
        family_count=len(families),
    )
    intersection_queries = tuple(
        item.text for item in request.queries if item.purpose == "intersection"
    )
    return SearchPlan(
        searchable=searchable,
        intent=request.facets[0] if request.facets else "archive_lookup",
        core_concepts=tuple(topic_anchors) or (request.normalized_question,),
        aliases=(),
        optional_concepts=(),
        entity_types=(),
        query_families=tuple(families),
        phrases=(),
        exclude_terms=(),
        low_information_terms=(),
        reply_context=True,
        required_aspects=tuple(dict.fromkeys(("topic", *request.facets))) if searchable else (),
        normalized_intent=request.facets[0] if request.facets else "archive_lookup",
        topic_anchors=tuple(topic_anchors),
        topic_anchor_groups=tuple(mandatory_groups),
        answer_facets=request.facets,
        intersection_queries=intersection_queries,
        expected_evidence_pattern=str(policy.expected_evidence_pattern),
        retrieval_policy=policy,
    )


def _family_purpose(value: str) -> str:
    return {
        "topic": FamilyPurpose.TOPIC,
        "entity": FamilyPurpose.ENTITY,
        "facet": FamilyPurpose.FACET,
        "intersection": FamilyPurpose.INTERSECTION,
        "semantic": FamilyPurpose.TERMINOLOGY,
        "constraint": FamilyPurpose.OTHER,
    }.get(str(value), FamilyPurpose.OTHER)


def _evidence_item(candidate, rank: int) -> EvidenceItem:
    message = candidate.message
    context_text = "\n".join(
        item.text_raw for item in candidate.context[:8] if getattr(item, "text_raw", None)
    ) or None
    message_id = message.message_id if message.message_id is not None else f"p{message.source_page}o{message.source_order}"
    return EvidenceItem(
        evidence_id=f"archive:{message_id}",
        source_type=SourceType.ARCHIVE,
        source_name="DrJavan Telegram Archive",
        source_ref=message.source_locator or f"{message.source_file}#p{message.source_page}o{message.source_order}",
        text=message.text_raw or "",
        title="Telegram discussion",
        context=context_text,
        timestamp=getattr(message, "datetime_iso", None) or getattr(message, "datetime_raw", None),
        author_or_org=message.author,
        retrieval_score=float(candidate.local_score),
        semantic_score=None,
        metadata={
            "rank": rank,
            "matched_terms": tuple(candidate.matched_terms),
            "match_reasons": tuple(candidate.match_reasons),
            "cluster_key": candidate.cluster_key,
            "cluster_size": candidate.cluster_size,
            "context_count": len(candidate.context),
        },
        citation_capability="message_id_exact_quote",
        trust_tier="community_archive",
    )


__all__ = ["ArchiveRetrievalProvider", "archive_plan_from_request"]
