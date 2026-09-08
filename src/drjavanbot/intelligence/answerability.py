from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Iterable

from drjavanbot.normalization import normalize_text
from .facets import evidence_signal
from .models import (
    EvidenceItem,
    FacetCoverage,
    FreshnessClass,
    QuestionUnderstanding,
    RequestedFactCoverage,
    SourceRoute,
    SourceType,
)

_CONFLICT_MARKERS = ("اما", "ولی", "مخالف", "اشتباه", "نه", "however", "disagree", "incorrect", "conflict")


def assess_requested_fact_coverage(
    understanding: QuestionUnderstanding,
    route: SourceRoute,
    evidence: Iterable[EvidenceItem],
    *,
    now: datetime | None = None,
) -> RequestedFactCoverage:
    items = tuple(evidence)
    now = now or datetime.now(timezone.utc)
    topic_items = tuple(item for item in items if _topic_match(understanding, item))
    topic_present = bool(topic_items)

    facet_results: list[FacetCoverage] = []
    for facet in understanding.facets:
        best = (False, 0.0, "facet_semantic_signal_missing")
        for item in topic_items:
            signal = evidence_signal(_combined_text(item), facet)
            if signal[1] > best[1]:
                best = signal
            if signal[0] and signal[1] >= 0.9:
                break
        facet_results.append(FacetCoverage(
            facet=facet,
            supported=bool(best[0]),
            score=float(best[1]),
            signal_code=str(best[2]),
        ))

    requested_fact_supported = topic_present and (
        all(item.supported for item in facet_results) if facet_results else True
    )
    direct_items = [item for item in topic_items if item.text and any(
        result.supported and evidence_signal(item.text, result.facet)[0]
        for result in facet_results
    )]
    if direct_items:
        directness = "direct"
    elif requested_fact_supported:
        directness = "contextual"
    else:
        directness = "none"

    independent_sources = len({
        (item.source_type, item.source_name, item.source_ref)
        for item in topic_items
    })
    conflicts = _has_conflict(topic_items)
    freshness_satisfied = _freshness_satisfied(understanding, topic_items, now=now)
    source_requirement_satisfied = _required_sources_satisfied(route, items)

    if not items:
        answerable, reason = False, "no_evidence"
    elif not topic_present:
        answerable, reason = False, "topic_missing"
    elif not requested_fact_supported:
        answerable, reason = False, "requested_fact_missing"
    elif not freshness_satisfied:
        answerable, reason = False, "freshness_requirement_not_met"
    elif not source_requirement_satisfied:
        answerable, reason = False, "required_source_missing"
    else:
        answerable, reason = True, "requested_fact_supported"

    return RequestedFactCoverage(
        topic_present=topic_present,
        requested_facets=tuple(facet_results),
        requested_fact_supported=requested_fact_supported,
        evidence_directness=directness,
        evidence_count=len(items),
        independent_sources=independent_sources,
        conflicts=conflicts,
        freshness_satisfied=freshness_satisfied,
        source_requirement_satisfied=source_requirement_satisfied,
        answerable=answerable,
        reason_code=reason,
    )


def _topic_match(understanding: QuestionUnderstanding, item: EvidenceItem) -> bool:
    text = normalize_text(_combined_text(item))
    required = [entity for entity in understanding.entities if not entity.inferred and entity.entity_type not in {"career_stage"}]
    if not required:
        return bool(text)
    for entity in required:
        variants = tuple(
            normalize_text(value)
            for value in (entity.canonical_label, entity.text, *entity.variants)
            if normalize_text(value)
        )
        if variants and not any(_contains_term(text, value) for value in variants):
            return False
    return True


def _combined_text(item: EvidenceItem) -> str:
    return "\n".join(value for value in (item.title, item.text, item.context) if value)


def _contains_term(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return re.search(rf"(?<![\w\u0600-\u06ff]){re.escape(term)}(?![\w\u0600-\u06ff])", text) is not None


def _has_conflict(items: tuple[EvidenceItem, ...]) -> bool:
    joined = normalize_text("\n".join(_combined_text(item) for item in items)).casefold()
    return any(normalize_text(marker) in joined for marker in _CONFLICT_MARKERS)


def _required_sources_satisfied(route: SourceRoute, items: tuple[EvidenceItem, ...]) -> bool:
    present = {str(item.source_type) for item in items}
    return all(source in present for source in route.required_sources)


def _freshness_satisfied(
    understanding: QuestionUnderstanding,
    items: tuple[EvidenceItem, ...],
    *,
    now: datetime,
) -> bool:
    if understanding.freshness not in {FreshnessClass.CURRENT, FreshnessClass.RECENT, FreshnessClass.REALTIME}:
        return True
    for item in items:
        if item.source_type in {SourceType.CURRENT_WEB, SourceType.OFFICIAL}:
            if item.timestamp is None:
                # Current-web adapters are obligated by their RetrievalRequest to
                # enforce freshness before admitting evidence.
                return True
            parsed = _parse_timestamp(item.timestamp)
            if parsed is not None and (now - parsed).days <= 180:
                return True
        parsed = _parse_timestamp(item.timestamp)
        if parsed is not None and (now - parsed).days <= 180:
            return True
    return False


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    for parser in (
        lambda: datetime.fromisoformat(raw),
        lambda: datetime.strptime(raw[:10], "%Y-%m-%d"),
    ):
        try:
            parsed = parser()
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


__all__ = ["assess_requested_fact_coverage"]
