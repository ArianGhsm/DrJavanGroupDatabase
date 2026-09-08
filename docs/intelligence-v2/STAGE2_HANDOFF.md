# DrJavanBot Dental Intelligence v2 — Stage 2 Handoff

## Purpose

Stage 1 established the typed pre-search intelligence and source-neutral evidence contracts. Stage 2 must implement the real knowledge/current source adapters and final multi-source answer path without weakening grounding, source attribution or clinical safety.

## Frozen Stage 1 contracts

Treat these as compatibility contracts unless a version bump and migration tests are included:

- Question Understanding schema: `question-intelligence-v2.0`
- Retrieval provider contract: `retrieval-provider-v2.0`
- Evidence contract: `evidence-v2.0`
- Requested Fact contract: `requested-fact-v2.0`
- `SourceType`: ARCHIVE / DENTAL_KNOWLEDGE / SCIENTIFIC / CURRENT_WEB / OFFICIAL / NONE

Every adapter implements `retrieve(RetrievalRequest) -> RetrievalResult`. Every factual source item must become an `EvidenceItem` with source type/name/ref, text, timestamp when applicable, retrieval/semantic scores when available, citation capability, metadata and trust tier.

## Stage 2 required work

### 1. Curated Dental Knowledge adapter

Implement a versioned, reviewable dental KB containing concepts/facts with explicit citations and provenance. The terminology lexicon must remain terminology-only; do not place answers in `dental_concepts.json`. KB records must carry source/version/date and support requested-fact matching. Define update/review policy and rollbackable data versioning.

### 2. Scientific retrieval adapter

Implement authoritative literature retrieval, preferably PubMed-first for biomedical publications plus specialty guidelines/organizations where applicable. Build source-aware citations from publication metadata/PMID/DOI. Route query terms from resolved concepts + requested facets + constraints. Cache metadata safely; do not persist user questions or raw private archive text in external systems.

### 3. Current Web adapter

Implement fresh web retrieval for salary, prices, market/career and other time-sensitive information. Enforce geography/time constraints in the adapter before admitting evidence. Evidence must carry retrieval timestamp and original source URL/reference.

### 4. Official adapter

Implement official-source discovery/verification for regulation, licensing, current official policies and authoritative guidelines. For regulation/current official facets, satisfy the `required` source requirement; a general web result alone must not impersonate official evidence.

### 5. Source orchestration

Execute required sources first, optional sources second, with bounded parallelism and explicit timeouts. A required-source failure must be represented as unavailable, not silently dropped. Preserve route rationale codes and source requirements in telemetry.

### 6. Fusion and reranking

Pipeline target:

```text
source-local retrieval
 -> concept/topic mandatory filter
 -> source-local normalized scores
 -> optional lexical+dense fusion
 -> requested-fact reranker
 -> source diversity/conflict handling
 -> Evidence Builder
```

Archive mandatory topic groups remain AND across concepts / OR within aliases. Requested-fact scoring must use the facet ontology and source metadata, not generic keyword family presence.

### 7. Dense benchmark before enablement

Do not enable `DRJAVAN_HYBRID_RETRIEVAL_V2` by default until a local/private benchmark is run. Initial candidate: locally cached `intfloat/multilingual-e5-small`; evaluate at least one stronger multilingual alternative if RAM permits. Compare improved lexical+concept baseline against BM25+dense fusion (RRF or measured alternative) on real vocabulary-mismatch cases.

Record Recall@K, MRR, irrelevant rate, p50/p95 latency, index size, RAM, incremental indexing time and reindex strategy. Raw Telegram archive must not be sent to an external embedding API.

### 8. Multi-source RequestedFactCoverage

For every required facet enforce `requested_fact_supported`. Examples:

- prevalence/frequency: commonness/frequency semantics or equivalent quantitative evidence;
- salary/cost: compensation/price context plus numeric evidence and current freshness;
- dosage: dose units/number and medication context;
- age/duration: numeric/temporal evidence;
- regulation/guideline: appropriate official/scientific source requirement and date/version.

Required source types and freshness must be satisfied before answerability=true.

### 9. Grounded multi-source synthesis

Generalize the existing claim-level grounding contract instead of replacing it. Every rendered factual claim must have one or more verified supports tied to an EvidenceItem/source ref. Citation validator must be source-aware. Unsupported model knowledge remains disabled as a factual authority.

Suggested source policy:

1. User-requested archive opinion -> Archive required.
2. Evergreen dental fact -> Curated KB required when covered; Scientific corroboration/fallback.
3. Current market/career -> Current Web required, Official optional where applicable.
4. Regulation/current official policy -> Official required.
5. Explicit hybrid -> all explicitly requested source classes required.
6. Unsupported model memory -> never satisfies RequestedFactCoverage.

### 10. Conflict rendering

When archive/community claims conflict with scientific or official evidence, do not merge them into false consensus. Render source-specific sections/claims and identify the disagreement with citations. Clinical safety should prioritize authoritative evidence for actionable guidance while still accurately reporting archive opinion when requested.

### 11. UI/citations

Update Telegram response rendering to label claim provenance, e.g. group archive / curated knowledge / scientific literature / current web / official. Preserve the existing source viewer for archive messages and add source-aware external citation navigation without exposing internal prompts/reasoning.

### 12. Telemetry

Persist only categorical/metadata fields: understanding success, route/source classes, facets, query/candidate/rerank counts, RequestedFactCoverage, answerability, model stage, model name, finish_reason, token usage, latency, structured-output status, fallback and cost. Never store raw questions, prompts, model responses, archive text or secrets.

## Stage 2 provider/model policy

Use `ModelPolicy` as the stage decision point. FAST tier: straightforward classification/rewrite. REASONING tier: ambiguity, multi-facet/high-stakes understanding, difficult evidence selection/synthesis/verification. Keep provider adapters separate from intelligence contracts.

Before depending on native strict structured output or provider-specific thinking controls, feature-detect/verify the provider capability. Always retain local schema validation, finish_reason handling and deterministic fallback.

## Stage 2 tests/gates

Required additions:

- real adapter unit/integration tests with deterministic fixtures;
- source outage and partial-source failure tests;
- current/freshness expiry tests;
- official-vs-web requirement tests;
- claim-level cross-source citation validation;
- archive vs scientific disagreement rendering;
- scientific/current no-evidence precision;
- 65-case vNext routing suite retained;
- scientific correctness subset with authoritative gold sources;
- real archive known-answerable cases retained;
- dense A/B benchmark if dense is proposed for enablement.

Do not merge Stage 2 unless full legacy suite remains green and no required-source/freshness/grounding contract is weakened.

## Stage 2 rollout

1. Merge adapters with flags disabled.
2. Shadow-run Question Intelligence + routing telemetry without changing user answers.
3. Enable curated/scientific path for a bounded internal cohort.
4. Enable current/official path after freshness/citation gates pass.
5. Enable multi-source renderer.
6. Consider dense only after its independent benchmark gate passes.
7. Keep archive-only compatibility rollback available throughout rollout.
