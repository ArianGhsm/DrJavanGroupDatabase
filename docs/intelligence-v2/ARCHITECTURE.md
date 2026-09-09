# Intelligence v2 Architecture

## Product contract

DrJavanBot is a **Dental Intelligence Assistant with privileged DrJavan Telegram Archive access**. The archive is a privileged source, not the universal factual authority. Unsupported model memory is never an independent factual source.

## Decision and evidence flow

```text
Question + bounded conversation interpretation context
  -> Question Intelligence v2
     intent / domain / entities / facets / constraints / geography / freshness
  -> Source Router v2
     Archive | Scientific | Current | Official | optional Curated Knowledge
  -> source-specific Query Generation
  -> required-source retrieval in parallel
     Archive: SQLite FTS5 + reply/discussion graph
     Scientific: PubMed E-utilities metadata + abstract
     Current: current web pages with geography/date/quality admission
     Official: authoritative-domain-only current retrieval
  -> Multi-Source Evidence Fusion
     topic relevance != requested-fact relevance
     trust + directness + freshness + independence + intent-sensitive source weight
  -> RequestedFactCoverage / Answerability v2
  -> Compact Grounded Synthesizer (claim text + opaque support IDs only)
  -> application-owned verbatim supports + claim-level Multi-Source Grounding Validator
  -> route-aware Telegram renderer + citations
```

## Question Intelligence

The deterministic parser is the normal fast path. It handles Persian/English/mixed text, dental terminology, transliteration, common typo variants, homonyms such as `کیست`, source intent, currentness and geography. A model call is reserved for unresolved ambiguity, difficult mixed/multi-facet interpretation, or other low-confidence cases. Conversation context contains bounded semantic state only and can aid interpretation; it is never evidence.

The public schema remains `question-intelligence-v2.0`. Model-produced facets are ontology-locked; unknown facets cannot enter routing or answerability. Resolved homonyms do not automatically trigger expensive reasoning.

## Retrieval providers

All providers implement the Stage 1 `RetrievalProvider` / `RetrievalRequest` / `RetrievalResult` / `EvidenceItem` contracts.

### Archive

`ArchiveRetrievalProvider` reads the canonical SQLite index read-only. It preserves the mature FTS5/BM25/fuzzy/concept/reply/discussion behavior and exports Telegram messages as generic `EvidenceItem`s. Raw Telegram HTML is never rewritten by answering.

### Scientific

`PubMedScientificProvider` uses NCBI E-utilities. It retrieves PubMed IDs, title, authors, journal, publication year/type, DOI where available, and abstract text. It does not persist full copyrighted articles. Publication type contributes to trust/methodological strength: guideline/systematic review > review/RCT > single primary study.

### Current and Official

`CurrentInformationProvider` performs current/localized discovery and fetches the original page before admission. Salary/cost evidence must contain a real monetary signal, not merely a year or percentage. Current evidence is geography/date/trust gated. Page extraction keeps bounded verbatim windows around monetary facts so late-page tables/FAQ data are not lost to head truncation.

`OfficialInformationProvider` inherits the current-page mechanics but only admits allowlisted authoritative domains for the relevant geography. A non-official seed can never be relabeled official.

## Curated dental knowledge decision

Stage 2 deliberately does **not** populate a hand-authored factual KB. The Stage 1 terminology lexicon remains terminology-only. Evergreen factual questions use scientific retrieval plus source-aware caching. This avoids silently converting model memory or unreviewed facts into a new authority layer. The `DENTAL_KNOWLEDGE` provider contract remains optional/unavailable until a separately versioned, provenance-backed, reviewable dataset exists.

## Query generation

Query generation is source-specific:

- Archive: Persian colloquial/formal variants, English aliases, abbreviations, transliterations, concept anchors, facets, intersections and reply graph.
- Scientific: canonical English concepts, facet-specific terminology and review/guideline modifiers when appropriate.
- Current: current Gregorian/Persian year, geography, local employment/market terminology and current cost/salary language.
- Official: current topic + geography constrained to authoritative domains.

Queries are search hints only and never evidence.

## Fusion and answerability

`MultiSourceEvidenceFusion` keeps separate topic and requested-fact scores. Trust, freshness, source intent and independence are additional dimensions. Explicit archive questions strongly favor archive relevance; clinical facts favor scientific/official authority. Duplicate papers/pages cannot manufacture independent corroboration.

`RequestedFactCoverage` requires topic co-occurrence, requested facet evidence, required-source satisfaction and freshness. Current salary/cost claims require fresh current/official monetary evidence. Scientific factual claims cannot be authorized by Telegram messages. Unknown identifier guards prevent unrelated PubMed pages from making a fabricated entity answerable.

## Synthesis and grounding

The synthesizer receives only compact verbatim evidence spans plus opaque IDs and may output only claim text + selected support IDs. It does not output direct-answer metadata, quotes, source labels, confidence, counts or limitations. Application code maps IDs back to known EvidenceItems, derives claim kind/direct answer/presentation, and validates every numeric token against support. Unknown IDs, fabricated or non-verbatim support, unsupported amounts, missing required source/facet/date/currency and truncated output fail closed.

FAST synthesis uses the production DeepSeek V4 adapter with thinking disabled. Strong reasoning is reserved for genuine ambiguity, high-stakes requests or sufficiently multi-facet complexity; hybrid routing by itself does **not** force STRONG. Supported simple synthesis targets one call and at most one repair is permitted.

## Caching

`SourceAwareResponseCache` keys include Question Intelligence schema, router/fusion/grounding/retrieval contract versions, route/freshness/geography, model policy and archive fingerprint. TTL varies by route: archive can live longer, evergreen scientific is moderate, current is short and realtime is very short. Cache/grounding/retrieval contracts are `v2.1` in the stabilization candidate, so pre-stabilization multi-source cache entries cannot collide with the final schema.

## Dense retrieval

Dense retrieval remains disabled. Stage 1 did not establish a measured gain and the production host has no validated local `sentence_transformers` benchmark. No raw archive text is sent to an external embedding provider.
