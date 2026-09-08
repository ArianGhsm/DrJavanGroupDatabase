from __future__ import annotations

from .facets import facet_spec
from .models import (
    FreshnessClass,
    QuestionUnderstanding,
    SourceRequirement,
    SourceRoute,
    SourceSelection,
    SourceType,
)


def route_sources(understanding: QuestionUnderstanding) -> SourceRoute:
    selected: list[SourceSelection] = []
    reasons: list[str] = []

    def add(source: str, priority: int, requirement: str, freshness: str, reason: str, strategy: str) -> None:
        if any(item.source_type == source for item in selected):
            return
        selected.append(SourceSelection(
            source_type=str(source),
            priority=max(0, min(int(priority), 100)),
            requirement=str(requirement),
            freshness_requirement=str(freshness),
            rationale_code=reason,
            query_strategy=strategy,
        ))
        reasons.append(reason)

    facets = set(understanding.facets)
    if understanding.archive_specific:
        add(
            SourceType.ARCHIVE, 100, SourceRequirement.REQUIRED, FreshnessClass.UNSPECIFIED,
            "archive_opinion_request", "discussion_graph_with_concept_and_facet_queries",
        )

    if understanding.current_information_needed:
        add(
            SourceType.CURRENT_WEB, 100 if not understanding.archive_specific else 95,
            SourceRequirement.REQUIRED, FreshnessClass.CURRENT,
            "current_market_information" if facets & {"salary", "career", "cost", "product"} else "current_information_required",
            "fresh_web_query_with_geography_and_time_constraints",
        )
        official_required = bool(facets & {"regulation", "guideline"})
        add(
            SourceType.OFFICIAL, 96 if official_required else 88,
            SourceRequirement.REQUIRED if official_required else SourceRequirement.OPTIONAL,
            FreshnessClass.CURRENT,
            "official_current_authority" if official_required else "official_corrobation_preferred",
            "official_domain_or_registry_query",
        )

    if understanding.scientific_evidence_needed:
        # Curated dental knowledge gives a low-latency canonical answer when
        # available; scientific retrieval is the verification/fallback tier.
        add(
            SourceType.DENTAL_KNOWLEDGE, 100 if not understanding.archive_specific else 92,
            SourceRequirement.REQUIRED, understanding.freshness,
            "evergreen_scientific_factual", "canonical_concept_and_facet_lookup",
        )
        add(
            SourceType.SCIENTIFIC, 94 if not understanding.archive_specific else 90,
            SourceRequirement.OPTIONAL, understanding.freshness,
            "scientific_corroboration", "literature_query_with_concept_and_facet_terms",
        )

    if not selected:
        # Dentistry is the product default. A non-archive evergreen question is
        # routed to the curated knowledge tier instead of silently asking model memory.
        if understanding.domain == "dentistry":
            add(
                SourceType.DENTAL_KNOWLEDGE, 100, SourceRequirement.REQUIRED,
                understanding.freshness, "default_dental_knowledge",
                "canonical_concept_lookup",
            )
            add(
                SourceType.ARCHIVE, 35, SourceRequirement.OPTIONAL,
                FreshnessClass.UNSPECIFIED, "archive_supplementary",
                "discussion_graph_topic_lookup",
            )
        else:
            add(
                SourceType.NONE, 100, SourceRequirement.REQUIRED,
                FreshnessClass.UNSPECIFIED, "no_supported_source_route", "none",
            )

    # For standard scientific/current questions the archive is useful context,
    # but never allowed to satisfy the primary source requirement by itself.
    if not understanding.archive_specific and any(item.source_type != SourceType.ARCHIVE for item in selected):
        add(
            SourceType.ARCHIVE, 30, SourceRequirement.OPTIONAL,
            FreshnessClass.UNSPECIFIED, "archive_supplementary",
            "discussion_graph_topic_lookup",
        )

    selected.sort(key=lambda item: (-item.priority, item.source_type))
    fallback = tuple(item.source_type for item in selected if item.source_type != SourceType.NONE)
    return SourceRoute(
        selected_sources=tuple(selected),
        fallback_order=fallback,
        rationale_codes=tuple(dict.fromkeys(reasons)),
    )


def route_summary(route: SourceRoute) -> tuple[tuple[str, int, str, str], ...]:
    return tuple(
        (item.source_type, item.priority, item.requirement, item.rationale_code)
        for item in route.selected_sources
    )


__all__ = ["route_sources", "route_summary"]
