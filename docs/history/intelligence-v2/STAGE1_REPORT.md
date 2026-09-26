# DrJavanBot Dental Intelligence v2 — Stage 1 Report

## Scope and invariants

Stage 1 converts the decision layer from archive-only query planning into a typed Dental Intelligence core while preserving the current Telegram/archive answer path as the default compatibility mode. No production deployment, production database mutation, archive rewrite, cache clear, service restart, destructive migration, secret export, or external embedding of archive text was performed.

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Branch: `feature/intelligence-v2-stage1`
- Verified base/main SHA: `222fd6b952b63613400df2040007ace3af798a0c`
- Verified production SHA before and after implementation: `222fd6b952b63613400df2040007ace3af798a0c`
- Production service remained active on `/opt/drjavanbot/current`.
- GitHub remains source of truth for code.

## Stage 1 architecture

```text
User question
  -> Question Intelligence (typed/versioned; LLM-first when enabled, deterministic fallback)
  -> Dental Concept Resolver + Facet Ontology
  -> Source Router
  -> source-neutral RetrievalRequest[]
       -> Archive adapter (implemented)
       -> Dental Knowledge adapter (Stage 2)
       -> Scientific adapter (Stage 2)
       -> Current Web adapter (Stage 2)
       -> Official adapter (Stage 2)
  -> EvidenceItem[]
  -> facet-aware reranking / RequestedFactCoverage
  -> Grounded synthesis (existing archive path in Stage 1; multi-source synthesis is Stage 2)
```

The Stage 1 runtime integration is feature-flagged. When v2 is enabled, Question Intelligence may run before archive search and its validated archive plan can replace the legacy search-planner call. When Source Router v2 marks a non-archive source as required, Stage 1 fails closed instead of fabricating an archive-only or model-memory answer; multi-source execution is completed in Stage 2.

## Implemented components

### Question Intelligence

`src/drjavanbot/intelligence/models.py`, `understanding.py`, `planning.py`, `model_policy.py`, and `provider.py` implement a versioned semantic contract with language profile, domain/subdomain, intent, entities, requested facets, constraints, geography, freshness, source preferences, archive/science/current requirements, ambiguity, confidence and safety class.

Deterministic rules contain language/intent semantics only. Dental answer facts are not placed in rules or lexicons. The LLM path returns compact structured classification, is parsed locally, then augmented with deterministic minimum guarantees. Provider/runtime failures fall back to deterministic understanding.

### Dental Facet Ontology

`facets.py` defines 30+ general facets including definition, classification, prevalence, frequency, epidemiology, age, sex, population, location, distribution, etiology/cause, risk factor, signs/symptoms, diagnosis/differential, radiographic/histopathology, indication/contraindication, treatment, technique/method, dosage, timing/duration, comparison/recommendation, prognosis/recurrence/complication/follow-up, material/product, cost/salary/career, regulation/guideline and clinical decision.

Each facet carries semantic markers, requested-fact evidence markers/shape, freshness sensitivity and source affinity. Salary/cost/regulation are current-sensitive; dosage/age/duration require numeric evidence; prevalence requires commonness/frequency semantics rather than disease-name presence alone.

### Normalization and terminology

Text normalization preserves raw archive text and normalizes search representations. Persian/Arabic character forms, punctuation/digits, zero-width spacing, common filler/copula terms, plurals and comparative/superlative language are handled without blind dental stemming.

`DentalConceptResolver` separates terminology normalization from text normalization. It resolves canonical, Persian, English, abbreviation, transliteration, common typo and spelling variants. The regression family `odontogenic / ادنتوژنیک / اودنتوژنیک / اودونتوژنیک` resolves to the same concept family. `کیست` is contextually disambiguated between the lesion noun and the Persian who-is interrogative.

### Source Router

Typed source types are `ARCHIVE`, `DENTAL_KNOWLEDGE`, `SCIENTIFIC`, `CURRENT_WEB`, `OFFICIAL`, and `NONE`. Routes contain priority, required/optional status, freshness, reason code, query strategy and fallback order. Reason codes are inspectable product metadata, not chain-of-thought.

Examples validated by tests:

- `گروه درباره e.max چی گفته؟` -> ARCHIVE required.
- `شایع‌ترین کیست ادنتوژنیک چیست؟` -> DENTAL_KNOWLEDGE required, SCIENTIFIC corroboration, ARCHIVE supplementary.
- current new-graduate dentist salary in Iran -> CURRENT_WEB required, OFFICIAL preferred/required by policy where appropriate.
- group opinion + scientific/current request -> hybrid route.

### Retrieval contracts and archive adapter

`RetrievalProvider`, `RetrievalRequest`, `RetrievalResult` and `EvidenceItem` are source-neutral. The Archive adapter maps them to the existing SQLite FTS5/BM25/fuzzy/concept/discussion/reply stack without changing canonical archive storage.

Mandatory topic semantics were strengthened. Topic identity now uses AND across mandatory concept groups and OR within aliases of each concept. A partial OR hit or generic facet token such as `شایع`, `سن`, `پیشنهاد`, `بهترین`, or `حقوق` cannot manufacture topic anchoring.

### Requested-Fact Coverage / Answerability v2

`RequestedFactCoverage` records topic presence, per-facet support, directness, evidence count, independent source count, conflict, freshness and required-source satisfaction. Answerability fails closed when the requested fact is missing even if the topic is present.

This directly prevents the previous state where `topic_anchored=True` plus a generic family hit could become `answerable=True` without evidence for prevalence, salary, dose, timing, etc.

### Model policy and provider telemetry

`ModelPolicy` separates FAST and REASONING tiers by stage. Simple classification/rewrite can use FAST; ambiguity, multi-facet/high-stakes understanding and later synthesis/verification may use a reasoning model. The v2 Question Intelligence adapter does not force thinking off; provider-native support remains configurable and is not assumed universally.

`finish_reason` is now captured in provider results/telemetry. Length/max-token finishes are treated as truncation failures before structured parsing. Raw prompt, response, question text and archive evidence are not stored in telemetry.

## Required regression cases

All required understanding/routing regressions pass:

1. `کدام کیست‌های اودونتوژنیک رایج‌تر هستند؟`
2. `شایع‌ترین کیست ادنتوژنیک چیست؟`
3. `most common odontogenic cyst?`
4. `کدوم سیست اودنتوژنیک بیشتر دیده میشه؟`
5. `حقوق دانشجوهای تازه فارغ التحصیل شده چقدره؟`
6. `حقوق دندانپزشک تازه فارغ التحصیل در ایران چقدره؟`
7. `گروه درباره حقوق دندانپزشکا چی گفته؟`
8. `درباره حقوق دندانپزشک تازه‌کار هم نظر گروه رو بگو هم وضعیت الان ایران رو`
9. adversarial `علی کیست؟` does not resolve as a cyst.

## Quality Lab vNext

The Stage 1 vNext suite contains 65 deterministic evaluation questions across 17 discipline/category groups and 33 observed facets. It covers formal Persian, colloquial Persian, English, mixed language, spelling/transliteration variants, current-market/career/regulatory questions, archive-opinion requests, hybrid requests and adversarial ambiguity.

Latest result: **65/65 understanding + primary source routing contracts passed**.

This Stage 1 suite does not claim scientific-answer correctness because Scientific/Current-Web runtime sources and final multi-source synthesis are Stage 2 deliverables.

## Full tests

- Baseline before Stage 1: **264 passed**.
- Final Stage 1 compile: **PASS**.
- Final full suite: **288 passed**.
- Existing grounding, Telegram, owner-panel, updater, access-control, archive-index, citation and rollback tests remain present.

## Archive benchmark before/after

The frozen 37-case archive benchmark was executed against the same read-only production archive database to isolate code behavior from corpus changes.

| Metric | Base `222fd6b` | Stage 1 |
| --- | ---: | ---: |
| Cases | 37 | 37 |
| MRR | 0.8081 | 0.8081 |
| mean top-K topic relevance | 0.9265 | 0.9265 |
| mean irrelevant candidate rate | 0.0735 | 0.0735 |
| facet colocation rate | 1.0000 | 1.0000 |
| strict failures | 0 | 0 |
| p50 retrieval | 2647.9 ms | 2793.5 ms |
| p95 retrieval | 3181.2 ms | 3400.7 ms |

Ranking quality did not regress. The latency difference in this pair of runs is approximately 5-7% and remains under the existing 4 s p95 gate; it is treated as run variance until repeated benchmark samples establish otherwise. The legacy frozen suite does not exercise Source Router/Question Intelligence, so semantic improvement is measured by vNext rather than by claiming an artificial MRR gain.

## Dense retrieval verdict

Stage 1 includes an experimental local/private-friendly dense reranker contract using `intfloat/multilingual-e5-small` as the initial candidate model. The production environment does not currently have `sentence_transformers` installed, and no external embedding API was permitted to receive raw archive text. Therefore a valid A/B dense benchmark was **NOT VERIFIED** in Stage 1.

Decision: **do not enable dense retrieval in production yet**. Keep `DRJAVAN_HYBRID_RETRIEVAL_V2=false`. Stage 2 should benchmark a locally cached multilingual model on vocabulary-mismatch cases and enable dense fusion only if Recall@K/MRR gains are material without unacceptable latency/RAM cost.

## Feature flags

All default to false:

- `DRJAVAN_INTELLIGENCE_V2`
- `DRJAVAN_SOURCE_ROUTER_V2`
- `DRJAVAN_HYBRID_RETRIEVAL_V2`

This guarantees current production compatibility until Stage 2 source adapters and multi-source synthesis are ready.

## Production compatibility and safety

The raw Telegram archive is unchanged. Production data/database were not mutated. No cache was cleared. No service was restarted/stopped. No deployment or migration occurred. Secrets were neither printed nor committed. The production service remained on the verified base SHA throughout Stage 1.

## Known limitations

- DENTAL_KNOWLEDGE, SCIENTIFIC, CURRENT_WEB and OFFICIAL network/content adapters are contracts/stubs only.
- Final source-aware multi-source synthesis/citation rendering is not implemented in Stage 1.
- Curated dental KB content/versioning policy is Stage 2.
- Provider-native strict JSON Schema capability is not assumed; compact JSON + local validation/fallback is used.
- Dense retrieval benefit is not established and remains disabled.
- vNext currently measures understanding/routing, not scientific truth of final answers.

## Stage 2 entry gate

Stage 2 should start from this branch only after merge review. It must preserve fail-closed requested-fact/source requirements and existing claim-level grounding. Scientific/current sources must produce source-aware EvidenceItems and may not silently fall back to unsupported model knowledge.
