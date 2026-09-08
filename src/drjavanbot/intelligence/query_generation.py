from __future__ import annotations

from drjavanbot.normalization import normalize_text
from drjavanbot.search.terms import informative_query
from .facets import facet_spec
from .models import QuestionUnderstanding, RetrievalQuery, RetrievalRequest, SourceRoute, SourceType


MAX_QUERIES_PER_SOURCE = 18


def generate_retrieval_requests(
    understanding: QuestionUnderstanding,
    route: SourceRoute,
    *,
    top_k: int = 20,
) -> tuple[RetrievalRequest, ...]:
    requests: list[RetrievalRequest] = []
    for selection in route.selected_sources:
        if selection.source_type == SourceType.NONE:
            continue
        queries = _queries_for_source(understanding, selection.source_type)
        requests.append(RetrievalRequest(
            source_type=selection.source_type,
            normalized_question=understanding.normalized_question,
            queries=queries[:MAX_QUERIES_PER_SOURCE],
            entity_ids=tuple(item.canonical_id for item in understanding.entities),
            facets=understanding.facets,
            freshness=selection.freshness_requirement,
            geography=understanding.geography,
            top_k=max(1, min(int(top_k), 100)),
        ))
    return tuple(requests)


def _queries_for_source(understanding: QuestionUnderstanding, source_type: str) -> tuple[RetrievalQuery, ...]:
    if source_type == SourceType.ARCHIVE:
        return _archive_queries(understanding)

    out: list[RetrievalQuery] = []
    canonical_terms = [item.canonical_label for item in understanding.entities if item.canonical_label]
    topic = " ".join(canonical_terms[:3]) or informative_query(understanding.normalized_question)
    if topic:
        out.append(RetrievalQuery(topic, "topic", purpose="topic", priority=100, anchor=True, mandatory=True))
    for facet in understanding.facets[:4]:
        spec = facet_spec(facet)
        if spec is None:
            continue
        facet_term = next((value for value in spec.markers if value.isascii()), spec.markers[0] if spec.markers else facet)
        query = " ".join(value for value in (topic, facet_term) if value)
        if query:
            out.append(RetrievalQuery(query, f"facet_{facet}", purpose="intersection", priority=92, anchor=True, mandatory=False))
    if understanding.geography.label and source_type in {SourceType.CURRENT_WEB, SourceType.OFFICIAL}:
        query = " ".join(value for value in (topic, *understanding.facets[:2], understanding.geography.label) if value)
        out.append(RetrievalQuery(query, "geography", purpose="constraint", priority=95))
    out.append(RetrievalQuery(understanding.normalized_question, "semantic_rewrite", purpose="semantic", priority=90))
    return _dedupe(out)


def _archive_queries(understanding: QuestionUnderstanding) -> tuple[RetrievalQuery, ...]:
    out: list[RetrievalQuery] = []
    # Each resolved entity is a mandatory semantic anchor group. Variants inside
    # the family are alternatives; separate mandatory families must co-occur in
    # the same discussion before it can become topic-anchored.
    for index, entity in enumerate(understanding.entities[:4]):
        if entity.inferred and entity.entity_type in {"profession", "career_stage"}:
            continue
        variants = tuple(dict.fromkeys(
            normalize_text(value)
            for value in (entity.canonical_label, entity.text, *entity.variants)
            if normalize_text(value)
        ))
        for variant in variants[:4]:
            out.append(RetrievalQuery(
                variant,
                f"topic_{index}_{entity.canonical_id}",
                purpose="topic",
                priority=100 - index,
                anchor=True,
                mandatory=True,
            ))
    if not any(item.anchor for item in out):
        topical = informative_query(understanding.normalized_question)
        if topical:
            out.append(RetrievalQuery(topical, "topic", purpose="topic", priority=100, anchor=True, mandatory=True))

    topic_seed = next((item.text for item in out if item.anchor), "")
    for facet in understanding.facets[:5]:
        spec = facet_spec(facet)
        if spec is None:
            continue
        for marker in spec.evidence_markers[:3]:
            out.append(RetrievalQuery(marker, f"facet_{facet}", purpose="facet", priority=88))
        if topic_seed and spec.evidence_markers:
            out.append(RetrievalQuery(
                f"{topic_seed} {spec.evidence_markers[0]}",
                f"intersection_{facet}",
                purpose="intersection",
                priority=94,
                anchor=True,
                mandatory=False,
            ))
    return _dedupe(out)


def _dedupe(values: list[RetrievalQuery]) -> tuple[RetrievalQuery, ...]:
    out: list[RetrievalQuery] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        normalized = normalize_text(item.text)
        key = (item.family, normalized)
        if not normalized or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(out)


__all__ = ["MAX_QUERIES_PER_SOURCE", "generate_retrieval_requests"]
