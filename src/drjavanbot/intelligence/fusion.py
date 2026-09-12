from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from drjavanbot.normalization import normalize_text
from .facets import evidence_signal
from .models import EvidenceItem, FreshnessClass, QuestionUnderstanding, SourceRoute, SourceType


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    item: EvidenceItem
    topic_relevance: float
    requested_fact_relevance: float
    trust_score: float
    freshness_score: float
    source_intent_score: float
    fused_score: float


class MultiSourceEvidenceFusion:
    def rerank(
        self,
        understanding: QuestionUnderstanding,
        route: SourceRoute,
        items: tuple[EvidenceItem, ...],
        *,
        top_k: int = 20,
        now: datetime | None = None,
    ) -> tuple[RankedEvidence, ...]:
        now = now or datetime.now(timezone.utc)
        ranked = [self._score(understanding, route, item, now=now) for item in items]
        ranked.sort(key=lambda value: value.fused_score, reverse=True)
        # Duplicate papers/pages or repeated mirrors must not manufacture independent evidence.
        deduped: list[RankedEvidence] = []
        seen: set[str] = set()
        per_source: dict[str, int] = {}
        for value in ranked:
            key = value.item.independence_key or value.item.source_ref or value.item.evidence_id
            if key in seen:
                continue
            source = str(value.item.source_type)
            if per_source.get(source, 0) >= 10:
                continue
            seen.add(key); per_source[source] = per_source.get(source, 0) + 1
            deduped.append(value)
            if len(deduped) >= top_k:
                break
        return tuple(deduped)

    def _score(self, understanding: QuestionUnderstanding, route: SourceRoute, item: EvidenceItem, *, now: datetime) -> RankedEvidence:
        topic = _topic_relevance(understanding, item)
        fact = _fact_relevance(understanding, item)
        trust = float(item.trust_score if item.trust_score is not None else _trust_from_tier(item.trust_tier))
        freshness = _freshness_score(understanding, item, now)
        source_score = _source_intent_score(understanding, route, item)
        if understanding.facets:
            fused = 0.28 * topic + 0.32 * fact + 0.24 * trust + 0.11 * freshness + 0.05 * source_score
        else:
            fused = 0.44 * topic + 0.24 * trust + 0.15 * freshness + 0.17 * source_score
        if set(route.required_sources) == {SourceType.ARCHIVE}:
            fused = min(1.0, fused + 0.22) if item.source_type == SourceType.ARCHIVE else fused * 0.32
        if item.source_type == SourceType.ARCHIVE and set(understanding.facets) & {"recommendation", "comparison"}:
            fused += _archive_answer_signal(item.text or "")
        return RankedEvidence(item, topic, fact, trust, freshness, source_score, max(0.0, min(fused, 1.0)))


def _topic_relevance(understanding: QuestionUnderstanding, item: EvidenceItem) -> float:
    text = normalize_text(_scoring_text(item)).casefold()
    entities = [entity for entity in understanding.entities if not entity.inferred and entity.entity_type != "career_stage"]
    if not entities:
        return 0.78 if text else 0.0
    matched = 0
    for entity in entities:
        variants = tuple(dict.fromkeys(normalize_text(value).casefold() for value in (entity.text, entity.canonical_label, *entity.variants) if normalize_text(value)))
        if any(_term_match(text, variant) for variant in variants):
            matched += 1
    return matched / max(1, len(entities))


def _fact_relevance(understanding: QuestionUnderstanding, item: EvidenceItem) -> float:
    if not understanding.facets:
        return 0.75
    text = _scoring_text(item)
    facets = tuple(understanding.facets)
    if set(facets) & {"recommendation", "comparison"}:
        facets = tuple(facet for facet in facets if facet not in {"product", "material"})
    scores = [evidence_signal(text, facet)[1] for facet in facets]
    return sum(scores) / max(1, len(scores))


def _scoring_text(item: EvidenceItem) -> str:
    # Archive context is a collection of other messages with different source
    # locators. It is promoted separately by ArchiveRetrievalProvider, so using
    # it here would attribute a reply's relevance to the original question.
    values = (item.title, item.text) if item.source_type == SourceType.ARCHIVE else (item.title, item.text, item.context)
    return "\n".join(value for value in values if value)


def _archive_answer_signal(text: str) -> float:
    normalized = normalize_text(text).casefold()
    request_cues = (
        "کدوم", "کدام", "چه برند", "معرفی کنید", "پیشنهاد میدین",
        "پیشنهاد می دید", "ممنون میشم", "نظرتون چیه", "which", "recommend?",
    )
    experience_cues = (
        "کار کردم", "استفاده کردم", "راضی", "تجربه", "ترجیح", "عالی",
        "بهتره", "خوب بود", "پیشنهادم", "i use", "used", "satisfied",
    )
    signal = 0.16 if any(cue in normalized for cue in experience_cues) else 0.0
    if any(cue in normalized for cue in request_cues) or "?" in text or "؟" in text:
        signal -= 0.22
    return signal


def _trust_from_tier(tier: str) -> float:
    return {
        "official_authority": 0.98, "clinical_guideline": 1.0, "systematic_review": 0.97,
        "peer_reviewed_review": 0.90, "randomized_trial": 0.90, "peer_reviewed_primary": 0.78,
        "current_web": 0.58, "community_archive": 0.42,
    }.get(str(tier), 0.5)


def _freshness_score(understanding: QuestionUnderstanding, item: EvidenceItem, now: datetime) -> float:
    if understanding.freshness not in {FreshnessClass.CURRENT, FreshnessClass.RECENT, FreshnessClass.REALTIME}:
        if item.source_type == SourceType.SCIENTIFIC and item.publication_year:
            age = max(0, now.year - int(item.publication_year))
            return 1.0 if age <= 5 else 0.85 if age <= 10 else 0.7
        return 0.9
    current_signal = bool(item.metadata.get("current_year_signal"))
    if current_signal:
        return 1.0
    if not item.timestamp:
        return 0.0
    try:
        parsed = datetime.fromisoformat(item.timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        days = max(0, (now - parsed.astimezone(timezone.utc)).days)
    except Exception:
        return 0.0
    if days <= 30: return 1.0
    if days <= 90: return 0.9
    if days <= 180: return 0.72
    if days <= 365: return 0.35
    return 0.05


def _source_intent_score(understanding: QuestionUnderstanding, route: SourceRoute, item: EvidenceItem) -> float:
    source = str(item.source_type)
    required = set(route.required_sources)
    if source in required:
        return 1.0
    if understanding.archive_specific:
        return 0.95 if source == SourceType.ARCHIVE else 0.45
    if understanding.current_information_needed:
        return 0.95 if source in {SourceType.CURRENT_WEB, SourceType.OFFICIAL} else 0.35
    if understanding.scientific_evidence_needed:
        return 0.98 if source in {SourceType.SCIENTIFIC, SourceType.OFFICIAL, SourceType.DENTAL_KNOWLEDGE} else 0.35
    return 0.7


def _term_match(text: str, term: str) -> bool:
    if not term: return False
    if " " in term: return term in text
    return re.search(rf"(?<![\w\u0600-\u06ff]){re.escape(term)}(?![\w\u0600-\u06ff])", text) is not None


__all__ = ["RankedEvidence", "MultiSourceEvidenceFusion"]
