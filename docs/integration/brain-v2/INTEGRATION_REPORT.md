# Brain V2 Integration Report

## Scope and provenance

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Locked original base / main at integration start: `4226f2c30a04d89a28afe8284be5c3e5f657a2a6`
- Integration branch: `integration/brain-v2`
- Query Planner source: `parallel/brain-query-planner-v2` @ `56e54b7ffa0830529d94968ff8cdad65be6c21f1`
- Retrieval source: `parallel/brain-retrieval-engine-v2` @ `0f0d5b9c8f51b07bd4429ca398a4a70ddc366f6d`
- Evidence Reasoner source: `parallel/brain-evidence-reasoner-v2` @ `4e0e5a175809dd4607aa38cd6c964e5597aa54bb`
- Quality Lab source: `parallel/brain-quality-lab-v2` @ `2515e5ba92cee48699d3a3fd4d645cc0a417bdde`
- All four source branches were verified to have the locked original base as merge base.
- Source branches were merged with real ancestry in the requested order: planner -> retrieval -> evidence reasoner -> quality lab.
- No source branch was rebased, force-pushed, or deleted.
- Production was not deployed by this integration.

## Canonical integrated architecture

The final Brain V2 path is:

`normalized question -> typed AI search plan -> bounded multi-family local retrieval -> discussion graph/ranking -> adaptive context hydration -> retrieval quality assessment -> optional one bounded refinement -> discussion-first evidence pack -> answerability assessment -> atomic claim extraction -> deterministic support validation -> conditional semantic entailment verification -> local composition from verified archive claims -> citation validation -> response`.

Factual source-of-truth remains the Telegram archive. AI general knowledge can understand wording, suggest terminology, rank relevance, and test semantic entailment, but it is not factual evidence. A final factual claim must be bound to admitted archive evidence. Search hints, planner output, the user's question, and model memory are never evidence.

## Cross-branch conflicts and resolutions

The integration exposed semantic conflicts that did not appear in isolated branches. They were resolved at the owning boundary rather than by weakening tests:

- Typed planner metadata is now canonical for retrieval: `topic_anchors`, `answer_facets`, `anchor_families`, family `purpose`/`anchor`, evidence pattern, depth and bounded retrieval policy are consumed directly.
- Schema keys such as `timing_age` preserve underscores during facet mapping instead of being normalized into incompatible lookup keys.
- A requested facet that has no mapped family is represented as missing; it is not silently removed from the required-facet denominator.
- Bounded refined/corpus families may become topic anchors when a weak first pass discovers real archive vocabulary; generic rescue families do not automatically become anchors.
- `facet_complete_discussion` and `strong_direct_answer_candidate` stop unnecessary retrieval refinement. Recommendation/comparison plans that explicitly require multi-source evidence retain one bounded diversity rescue when coverage is too narrow.
- Direct plans can stop on a clustered, independently corroborated discussion rather than treating the single representative as a single weak message.
- Retrieval discussion identity and admitted context survive into evidence packing so downstream code does not re-bucket the same discussion inconsistently.
- Orchestrator answerability consumes RetrievalReport v2 quality state, topic-anchored discussion count, required-facet co-location, hydration and bridge signals.
- Provider/structured-output failures remain distinct from true archive insufficiency.
- The Quality Lab received an evaluation-only typed-plan adapter. Frozen gold identities and quality thresholds were not changed to make the candidate pass.

## Versions and cache invalidation

- Planner schema: `query-understanding-v1`
- Retrieval semantics: `discussion-retrieval-v2.1-typed-policy`
- Integration policy: `brain-v2-integration-policy-v6`
- Reasoning prompt: `evidence-reasoning-state-machine-v2.1`
- Validation semantics: `claim-support-validation-v2.2`
- Quality report schema: `quality-lab-v2.0`
- Hard application semantic-call ceiling: **4 logical AI calls** per uncached request.

The final-answer cache namespace includes normalized question, index fingerprint, planner version, retrieval semantics, integration policy, prompt/reasoning version, validation semantics, model and configuration signature. Stale pre-integration semantic answers therefore become cache misses even when the SQLite index is byte-identical. Planner cache remains schema/version/index/model aware. Cache failures are fail-soft and no destructive runtime migration is required.

Typical call paths remain bounded: cache hit 0; direct/extractive normally 2; direct plus verifier 3; faceted plus one refinement and extraction normally 3; complex repair/rescue/verifier paths remain <=4. There is no unbounded semantic loop.

## Quality Lab acceptance

Canonical migration QA run: GitHub Actions `34195520419`.

All workflow gates passed:

- package build/install: PASS (`drjavanbot-0.6.0` wheel)
- compileall: PASS
- SQLite FTS5: PASS
- `.env.example` shell compatibility: PASS
- full pytest: **238 passed in 9.11s**
- Quality Lab strict: **PASS**
- one-time legacy semantic benchmark cross-check: **PASS**
- systemd unit/fixed-path verification: **PASS**

Canonical Quality Lab (`quality-lab-v2.0`) metrics:

- archive files: **247**
- indexed messages: **249,907**
- DB size: **373,555,200 bytes**
- index build: **281.854 s**
- cases: **37**
- strict failures: **0**
- Discussion Recall@12 mean: **1.0000**
- MRR: **1.0000**
- topic relevance@12 mean: **1.0000**
- facet co-location rate: **1.0000**
- irrelevant candidate rate mean: **0.0000**
- median retrieval latency: **1531.958 ms**
- p95 retrieval latency: **1676.948 ms**
- median query count: **4**
- median hydration count: **9**
- scripted false-insufficient rate: **0.0000**
- scripted false-supported rate: **0.0000**
- grounding verifier accuracy: **1.0000**
- invalid citation rate: **0**
- quote mismatch rate: **0**
- unsupported high-risk claim rate: **0**
- grounding red-team: **12/12 passed**
- scripted E2E: **5/5 passed**
- scripted reason-code accuracy: **1.0000**
- fragmented recovery: **1.0000**

The one-time legacy cross-check also passed with `strict_failures=[]`; median retrieval was **1459.438 ms**, p95 **1783.302 ms**, and its complete-archive index build was **279.098 s**. This second full index was migration QA only; final default CI returns to the canonical single Quality Lab full-archive index.

## Important regressions

### Composite recommendation

Question class includes `کدوم برند کامپوزیت خوبه؟`.

Canonical Quality Lab result:

- frozen discussion recovered: YES
- first relevant discussion rank: **1**
- Discussion Recall@12: **1.0**
- topic relevance: **1.0**
- required facet co-location: complete
- irrelevant candidate rate: **0.0**
- independent-author diversity: **12**
- gate: PASS

No product/brand answer was hard-coded. Any final answer still has to be synthesized only from validated archive messages.

### Pediatric orthodontic timing

Question class includes `برای بچه‌ها ارتودنسی از چه سنی؟` / `در بچه‌ها ارتودنسی رو در چه سنی باید استفاده کرد؟`.

Canonical Quality Lab result:

- frozen discussion recovered: YES
- first relevant discussion rank: **1**
- Discussion Recall@12: **1.0**
- topic relevance@12: **1.0**
- required population/timing facets: **2/2 colocated in the same discussion**
- irrelevant candidate rate: **0.0**
- independent-author diversity: **12**
- gate: PASS

Relative to the frozen BASE Quality Lab, this moved first relevant rank from **8 -> 1**, topic relevance from **0.5833 -> 1.0000**, and irrelevant rate from **0.4167 -> 0.0000** while preserving frozen discussion recall.

### Short RCT

- frozen discussion recovered: YES
- first relevant rank: **1**
- recall: **1.0**
- topic relevance: **1.0**
- irrelevant rate: **0.0**
- gate: PASS

The evaluation adapter separates an ambiguous short acronym from supplied expanded aliases for ranking corroboration without encoding an answer fact.

## Performance delta versus locked BASE

Quality Lab BASE snapshot reported:

- MRR: **0.7812**
- topic relevance: **0.8958**
- irrelevant rate: **0.1042**
- median retrieval: **2869.332 ms**
- recall: **1.0000**

Integrated migration QA reported:

- MRR: **1.0000**
- topic relevance: **1.0000**
- irrelevant rate: **0.0000**
- median retrieval: **1531.958 ms**
- recall: **1.0000**

Median retrieval improved by approximately **46.6%** on these runs while preserving frozen recall and improving ranking/noise metrics. Wall-clock performance is runner-sensitive; correctness gates remain authoritative.

## Grounding and evidence safety

Evidence reasoning remains fail-closed:

- nonexistent evidence message IDs are rejected;
- quotes must be exact substrings of the cited admitted message;
- invented numeric/age/dose claims are rejected;
- unsupported brand/model literals are rejected;
- added/dropped negation and reversed comparison direction are rejected;
- topical citations cannot launder unsupported paraphrases;
- semantic paraphrases require bounded entailment verification when deterministic validation is insufficient;
- final composition uses only verified claims;
- model `insufficient_evidence` is not accepted blindly when a bounded facet-rich discussion pack is demonstrably answerable;
- true insufficient evidence remains a valid result;
- private chain-of-thought is neither persisted nor exposed.

Telemetry is bounded metadata only: stage, result/reason class, model/usage/latency/call number, evidence/discussion counts and retrieval quality. Raw archive text, prompts, raw model responses, secrets and unnecessary identities are not persisted as telemetry.

## Deployment / package impact

- SQLite schema: unchanged
- index format: unchanged
- DB migration: **NONE**
- external vector database: none
- external embedding service: none
- heavy new dependency: none
- production path/systemd redesign: none
- updater architecture redesign: none
- package install smoke: PASS
- systemd verification: PASS

The existing self-updater is sufficient for this code-only release because no new runtime schema or bootstrap-only service change is introduced. Production deployment is intentionally outside this integration and was not performed here.

## Archive integrity

BASE-to-integration comparison was inspected and contains no changed path below `گروه دکتر جوان/`. The integration did not edit, normalize, move, redact or regenerate `messages*.html`.

**Raw archive modified: NO.**

## Known limitations / residual risks

1. The Quality Lab has 37 cases, but only four real-archive cases are frozen as strict known-answerable discussion gold; many other real-archive cases remain observational to avoid inventing clinical truth.
2. High-volume pediatric retrieval can reach `MAX_DISCUSSIONS=140` in the legacy diagnostic pool. Canonical top-12 precision, co-location and frozen recall are currently perfect, but a larger manually adjudicated discussion-gold set would improve recall measurement.
3. The legacy semantic benchmark and RetrievalReport v2 use different compatibility proxies for facet state in some paths. In the one-time pediatric legacy run, the legacy text-bundle proxy observed complete 2/2 co-location while its internal legacy-compatible quality-state label remained `only_topical_facet_missing`. The canonical typed Quality Lab is authoritative and reports complete co-location. The legacy strict gate still passed.
4. CI intentionally does not require live AvalAI/DeepSeek secrets. Provider behavior is exercised with deterministic fake-provider E2E and grounding red-team tests; production provider failures remain fail-soft and are not cached as archive insufficiency.
5. Conversation/discussion clustering is bounded and deterministic rather than a learned global graph. This is intentional for auditability, latency and token control.

## Final integration decision

The integrated candidate is acceptable for merge only after the final clean PR workflow (canonical Quality Lab, pytest, compile, FTS5, env and systemd gates) passes on the documentation/cleanup head and a final BASE-to-head archive-integrity comparison again confirms no raw archive changes. No production deployment is performed by this integration.
