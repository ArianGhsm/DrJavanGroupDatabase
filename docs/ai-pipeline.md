# AI pipeline and runtime interfaces — Intelligence v2

DrJavanBot is a multi-source Dental Intelligence Assistant. Model memory is never factual evidence. The detailed design is in `docs/intelligence-v2/ARCHITECTURE.md`.

## Production pipeline

```text
question + bounded semantic conversation context
  -> deterministic Question Intelligence fast path
     -> optional model clarification only for genuine ambiguity/complexity
  -> Source Router v2
  -> source-specific queries
  -> required source providers in parallel
  -> Evidence Fusion (topic vs requested-fact relevance)
  -> RequestedFactCoverage / freshness / source authority
  -> compact grounded synthesis: claims + opaque support_ids only
  -> application-owned verbatim support/citation validation
  -> source-aware AnswerResult
```

Archive retrieval still uses the canonical SQLite/FTS5 + reply/discussion stack. Scientific retrieval uses PubMed E-utilities. Current/Official retrieval fetches original pages and applies geography/date/trust admission. The optional curated-knowledge provider remains unavailable until a provenance-backed factual dataset exists.

## AI call budget

Simple Question Intelligence is deterministic and normally uses **0 calls**. A supported answer normally uses **1 synthesis call**. One bounded structured/grounding repair is permitted. Ambiguous/complex/high-stakes stages can use the strong model. There are no unbounded semantic loops.

FAST stages use `deepseek-v4-flash` with thinking disabled. Strong stages default to `deepseek-v4-pro` with configurable reasoning effort. `finish_reason` is recorded and `length/max_tokens/token_limit` fails closed before parsing.

## Query generation

Queries differ by source: archive uses Persian/English/transliteration and reply graph; scientific uses canonical English/facet terms and review/guideline modifiers; current uses current year/geography/local market terminology; official adds authoritative-domain constraints. Queries are search hints, never evidence.

## Grounding

The model never writes quote text. Every factual claim selects opaque support IDs; the application maps those IDs to exact verbatim spans from supplied `EvidenceItem`s. Scientific/current/archive source classes are not interchangeable. Salary/cost needs a monetary amount/range, currency and date/year. Current evidence that contains only a year or unrelated percentage is not sufficient.

## Cache

The Intelligence v2 cache is separate and source-aware; the stabilization candidate uses cache/grounding/retrieval contract `v2.1` to invalidate pre-stabilization answers. Its key includes QI schema, router/retrieval/fusion/grounding versions, route/freshness/geography, model policy and archive fingerprint. TTL is longer for archive, moderate for scientific and short for current/realtime. Legacy archive-only cache entries cannot collide.

## Runtime flags

`DRJAVAN_INTELLIGENCE_V2`, `DRJAVAN_SOURCE_ROUTER_V2`, and `DRJAVAN_HYBRID_RETRIEVAL_V2` control rollout. With Source Router v2 enabled, Archive/Scientific/Current/Hybrid routes all pass through Multi-Source Grounding v2. Turning the flags off restores the legacy archive-only compatibility path.

## QA

CI/unit tests require no production secrets and cover provider fixtures, routing, current admission, SSRF guard, cache identity, conversation context, grounding red-team and Telegram rendering. Live PubMed/current smoke is deployment-host-only. The complete raw archive Quality Lab builds an isolated temporary index and never mutates production data.
