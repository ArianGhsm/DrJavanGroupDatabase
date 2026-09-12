from __future__ import annotations

from .models import FreshnessClass, QuestionUnderstanding, SourceRequirement, SourceRoute, SourceSelection, SourceType


def route_sources(understanding: QuestionUnderstanding) -> SourceRoute:
    selected: list[SourceSelection] = []
    reasons: list[str] = []

    def add(source: str, priority: int, requirement: str, freshness: str, reason: str, strategy: str) -> None:
        if any(item.source_type == source for item in selected):
            return
        selected.append(SourceSelection(
            source_type=str(source), priority=max(0, min(int(priority), 100)), requirement=str(requirement),
            freshness_requirement=str(freshness), rationale_code=reason, query_strategy=strategy,
        ))
        reasons.append(reason)

    facets = set(understanding.facets)
    explicit_archive = bool(understanding.archive_specific)
    normalized = str(understanding.normalized_question or "").casefold()
    archive_only = any(marker in normalized for marker in (
        "فقط از گروه", "فقط تو گروه", "فقط در گروه", "فقط آرشیو",
        "only from group", "group only", "archive only",
    ))
    practical_archive = bool(
        not understanding.current_information_needed
        and not understanding.scientific_evidence_needed
        and str(understanding.intent) == "recommendation"
        and bool(facets & {"product", "material"})
    )
    if archive_only:
        add(SourceType.ARCHIVE, 100, SourceRequirement.REQUIRED, FreshnessClass.UNSPECIFIED,
            "explicit_archive_only", "discussion_graph_with_concept_and_facet_queries")
        return SourceRoute(tuple(selected), (SourceType.ARCHIVE,), tuple(reasons))
    if explicit_archive:
        add(SourceType.ARCHIVE, 100, SourceRequirement.REQUIRED, FreshnessClass.UNSPECIFIED,
            "archive_opinion_request", "discussion_graph_with_concept_and_facet_queries")
    elif practical_archive:
        add(SourceType.ARCHIVE, 100, SourceRequirement.REQUIRED, FreshnessClass.UNSPECIFIED,
            "practical_archive_recommendation", "discussion_graph_with_concept_and_facet_queries")

    if understanding.current_information_needed:
        official_required = bool(facets & {"regulation"})
        if official_required:
            add(SourceType.OFFICIAL, 100 if not explicit_archive else 97, SourceRequirement.REQUIRED,
                FreshnessClass.CURRENT, "official_current_authority", "official_domain_query")
            add(SourceType.CURRENT_WEB, 94 if not explicit_archive else 93, SourceRequirement.OPTIONAL,
                FreshnessClass.CURRENT, "current_web_corroboration", "fresh_localized_web_query")
        else:
            add(SourceType.CURRENT_WEB, 100 if not explicit_archive else 96, SourceRequirement.REQUIRED,
                FreshnessClass.CURRENT,
                "current_market_information" if facets & {"salary", "career", "cost", "product"} else "current_information_required",
                "fresh_localized_web_query")
            add(SourceType.OFFICIAL, 90, SourceRequirement.OPTIONAL, FreshnessClass.CURRENT,
                "official_corroboration_preferred", "official_domain_query")

    if understanding.scientific_evidence_needed:
        # Stage 2: PubMed/authoritative scientific evidence is the real factual authority.
        # Curated knowledge stays optional until a separately versioned/provenance-backed KB exists.
        add(SourceType.SCIENTIFIC, 100 if not explicit_archive else 96, SourceRequirement.REQUIRED,
            understanding.freshness, "scientific_authority_required", "pubmed_canonical_concept_facet_query")
        add(SourceType.DENTAL_KNOWLEDGE, 76, SourceRequirement.OPTIONAL, understanding.freshness,
            "curated_knowledge_optional", "versioned_curated_lookup_if_configured")
        if "guideline" in facets:
            add(SourceType.OFFICIAL, 94, SourceRequirement.OPTIONAL, FreshnessClass.RECENT,
                "guideline_official_corroboration", "official_guideline_query")

    if not selected:
        if understanding.domain == "dentistry" and not facets:
            add(SourceType.ARCHIVE, 100, SourceRequirement.REQUIRED, FreshnessClass.UNSPECIFIED,
                "terse_dental_archive_lookup", "discussion_graph_topic_lookup")
        elif understanding.domain == "dentistry":
            add(SourceType.SCIENTIFIC, 100, SourceRequirement.REQUIRED, understanding.freshness,
                "default_dental_scientific", "pubmed_canonical_concept_query")
        else:
            add(SourceType.NONE, 100, SourceRequirement.REQUIRED, FreshnessClass.UNSPECIFIED,
                "no_supported_source_route", "none")

    if not explicit_archive and any(item.source_type not in {SourceType.ARCHIVE, SourceType.NONE} for item in selected):
        add(SourceType.ARCHIVE, 30, SourceRequirement.OPTIONAL, FreshnessClass.UNSPECIFIED,
            "archive_supplementary", "discussion_graph_topic_lookup")

    selected.sort(key=lambda item: (-item.priority, item.source_type))
    fallback = tuple(item.source_type for item in selected if item.source_type != SourceType.NONE)
    return SourceRoute(tuple(selected), fallback, tuple(dict.fromkeys(reasons)))


def route_summary(route: SourceRoute) -> tuple[tuple[str, int, str, str], ...]:
    return tuple((item.source_type, item.priority, item.requirement, item.rationale_code) for item in route.selected_sources)


__all__ = ["route_sources", "route_summary"]
