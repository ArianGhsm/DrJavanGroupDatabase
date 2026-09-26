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

## Single answer pipeline

There are no rollout flags any more (`DRJAVAN_INTELLIGENCE_V2`, `DRJAVAN_SOURCE_ROUTER_V2` and `DRJAVAN_HYBRID_RETRIEVAL_V2` are ignored). Every question goes through one flow in `telegram/services.py`:

1. Question Intelligence runs (deterministic; AI only for ambiguous questions) and records conversation context.
2. Time-sensitive questions (route requires Current/Official: prices, salaries, regulation) use Multi-Source Grounding first; if that is insufficient the group archive answers.
3. All other questions are answered from the **group archive first** (archive planner → retrieval → grounded synthesis). Only when the archive has no sufficient answer and the route needs Scientific evidence does Multi-Source Grounding (PubMed) run as a fallback.
4. Follow-up questions whose topic comes from the conversation ("و قیمتش؟") search the archive with the Question Intelligence plan instead of the raw text.

`ANSWER_PIPELINE_VERSION` in `ai/config.py` is part of the answer-cache key; bump it when retrieval or answering semantics change.

## Archive search relevance

- Conversational filler (`کسی میدونه`, `نظر دکتر جوان درباره`, `چنده`, `میدید`, …) is removed before search and never becomes a mandatory topic anchor (`search/terms.py`).
- Numbers match across spellings (`هفتم` ↔ `7` ↔ `۷`) and colloquial vowels match (`دندان` ↔ `دندون`) (`search/numerals.py`).
- Ranking is dominated by the IDF-weighted share of the question present in a message, with normalized BM25 as tie-breaker; messages missing one token of a long query are still retrieved (`search/sqlite.py`).
- `X چیه؟` is a definition request only when nothing more specific (recommendation, opinion, …) is asked; `یعنی` is not definition evidence.

## QA

CI/unit tests require no production secrets and cover provider fixtures, routing, current admission, SSRF guard, cache identity, conversation context, grounding red-team and Telegram rendering. Live PubMed/current smoke is deployment-host-only. The complete raw archive Quality Lab builds an isolated temporary index and never mutates production data.
