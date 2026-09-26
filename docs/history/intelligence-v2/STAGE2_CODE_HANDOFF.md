# DrJavanBot Intelligence v2 — Stage 2 Code Handoff

## Verdict

**READY_FOR_DEPLOY=true** for Prompt 2 deployment review. This means the Stage 2 branch is a tested deploy candidate; it does **not** mean it has been merged or deployed. Prompt 1 performed no production deploy, no merge to `main`, no service restart, no feature-flag mutation and no production archive/database mutation.

## Repository / branch identity

- repository: `ArianGhsm/DrJavanGroupDatabase`
- branch: `feature/intelligence-v2-stage2`
- Stage 1 base: `730381029a46437f60bdd7a9c4ed8e425970287c`
- code stabilization SHA: `c7ff5d00ba9cc0521b3f30894eaff3305fa8bf36`
- code stabilization tree: `cb0e429b57d88ea1563e2f3c763295748d403081`
- GitHub `main` observed immediately before handoff: `222fd6b952b63613400df2040007ace3af798a0c`
- production/current observed: `222fd6b952b63613400df2040007ace3af798a0c`
- pre-deploy rollback target for Prompt 2: `222fd6b952b63613400df2040007ace3af798a0c`

The exact final branch tip after this documentation commit cannot be self-embedded in the same Git commit without changing that commit hash. Prompt 1 therefore records the exact immutable code SHA/tree above; after push, the final branch ref SHA/tree must be verified externally and is reported in the operator/final response.

## Stage 2 commits from Stage 1 base

1. `82ef3292780064186a516bacdf49a8c2a1de8eef` — `feat: complete multi-source dental intelligence runtime`
2. `59bb46e00c5c0fd4274b76c3db233c2c6412b32b` — `test: expand intelligence v2 quality and grounding gates`
3. `c7ff5d00ba9cc0521b3f30894eaff3305fa8bf36` — `fix: stabilize grounded multi-source synthesis`
4. final documentation/handoff commit — created after all gates below are green; exact pushed branch tip is verified externally.

## Stabilization design

### Compact synthesis

The model schema is reduced to `claims[{text,support_ids}]`. The model does not author quote text, citation metadata, source labels, direct-answer metadata, confidence, counts or limitations. Application code owns those fields.

### Grounding

- support IDs map to locally-created contiguous verbatim spans from known EvidenceItems;
- unknown support/evidence IDs fail closed;
- direct answer is derived from the first validated claim;
- required source classes must appear in grounded claims;
- scientific facts cannot be authorized by Telegram Archive;
- current salary cannot be authorized by old archive evidence;
- every numeric token in a claim must occur in validated support;
- numeric-span canonicalization is limited to the same EvidenceItem **and matching support context**;
- salary/cost direct answers require supported amount/range, currency/unit and data year/date;
- output truncation fails closed;
- one structured/grounding repair maximum; no third/blind retry.

### Deterministic paths / model policy

- simple QI: 0 AI calls;
- simple archive-only e.max and bare `RCT?`: deterministic rendering, 0 synthesis calls;
- supported scientific/current/hybrid target: 1 synthesis call;
- hybrid routing alone does not force STRONG;
- genuine ambiguity/high-stakes/3+ facet complexity can select STRONG;
- no-evidence stops before synthesis with 0 calls.

### Provider reliability

- PubMed: NCBI E-utilities, process-wide pacing, one bounded retry for transient 408/429/5xx/timeout/network failures;
- Current: full bounded page scan, contiguous monetary windows, geography/date/trust admission, SSRF/private-network rejection;
- Official: authoritative-domain admission only;
- dense retrieval remains disabled.

### Cache / telemetry

- cache contract: `intelligence-cache-v2.1`;
- grounding contract in cache identity: `multisource-grounding-v2.1`;
- retrieval contract in cache identity: `multisource-retrieval-v2.1`;
- pre-stabilization Stage 2 cache entries therefore cannot collide;
- telemetry records stage/model/logical_call/attempt/reason/finish_reason/latency/tokens/cost without raw question/prompt/response/PII.

## Required live smoke / repeated reliability gate

Fresh-cache gate on code SHA `c7ff5d00ba9cc0521b3f30894eaff3305fa8bf36`:

| Class | Runs | Result | AI calls/run | Source contract |
|---|---:|---|---:|---|
| archive e.max | 5 | 5/5 supported | 0 | Archive |
| hybrid e.max | 5 | 5/5 supported | 1 | Archive + Scientific |
| cyst prevalence | 3 | 3/3 supported | 1 | Scientific |
| Iran new-graduate salary | 3 | 3/3 supported | 1 | Current Web, amount + currency + year |
| `RCT?` | 3 | 3/3 supported | 0 | Archive |
| `zqv-99` | 3 | 3/3 insufficient | 0 | stopped before synthesis |

Aggregate:

- fresh runs: **22**
- failed runs: **0**
- repair runs: **0**
- structured failure rows: **0**
- model-call rows: **11**
- p50 latency: **3067.92 ms**
- p95 latency: **6608.33 ms**
- input tokens: **17,241**
- output tokens: **3,040**
- reported cost: **829.20 IRT**
- hybrid live model: `deepseek-v4-flash`

A prior pre-stabilization run exposed both structured numeric-support failures and one isolated long AvalAI response; these were not counted as final metrics. The final fresh matrix above has no supported simple run over 25 s.

## Cache-hit gate

A temporary source-aware cache was tested for all six classes. Every second request returned `cache_hit=true`, `ai_calls=0`, same source mode, with measured hit handling around **0.30–0.47 ms**. No production cache was used or mutated.

## Intelligence Lab

- cases: **125**
- required-facet recall: **125/125**
- exact facet-set: **125/125**
- source routing: **125/125**
- archive-intent contract: **125/125**
- Oral Pathology: **13**
- distinct facets: **35**
- categories A-J: all present
- failures: **0**

## Frozen Archive Quality Lab

Final run on the committed code candidate; archive source read-only, index isolated in a temporary DB:

- source files: **247**
- messages: **249,907**
- cases: **37**
- strict failures: **0**
- Recall@12: **1.0000**
- MRR: **1.0000**
- topic relevance@12: **1.0000**
- facet colocation: **1.0000**
- irrelevant candidate rate: **0.0000**
- false insufficient: **0.0000**
- false supported: **0.0000**
- median retrieval: **1295.348 ms**
- p95 retrieval: **1746.146 ms**
- query median: **4**
- hydration median: **9**
- grounding red-team: **12/12**, verifier accuracy **1.0000**
- scripted E2E: **5/5**
- temporary index build: **292.181 s**
- temp DB size: **373,555,200 bytes**

## Compile / tests

- `python -m compileall -q src`: **PASS**
- full pytest on code candidate: **322 passed**
- retry-specific regression: malformed once → exactly one repair and success; malformed twice → fail closed after 2 calls, no third call
- numeric adversarial regression: unrelated number from the same EvidenceItem is rejected unless support context matches

## Production integrity / preflight

Final Prompt 1 integrity check:

- service: `drjavanbot` active
- observed MainPID: `3770`
- observed restart count: `0`
- `/opt/drjavanbot/current` → `/opt/drjavanbot/releases/222fd6b952b63613400df2040007ace3af798a0c`
- production HEAD: `222fd6b952b63613400df2040007ace3af798a0c`
- production release worktree: only expected untracked `.deploy_commit`
- `DRJAVAN_INTELLIGENCE_V2`: absent
- `DRJAVAN_SOURCE_ROUTER_V2`: absent
- `DRJAVAN_HYBRID_RETRIEVAL_V2`: absent
- raw archive: **247** files; manifest SHA-256 `0b7fa86c1b402a5e2ec66878f5ba06a33d7ef88744c2e710cdd937cb56cfe560`; final manifest byte-identical to baseline
- production archive DB SHA-256: `87b60aeb6d1931ee41dec294731d7b0866f48213b6d8621ed77d32103a11a4a6`; unchanged from baseline
- production archive DB counts from preflight: 249,907 messages / 249,907 FTS rows
- AvalAI secret presence was verified without printing its value

No raw archive, secrets, runtime DB/cache or production data are intended to be in the Stage 2 Git diff. Final Git hygiene check must confirm this immediately before push.

## Files changed from Stage 1 base

Runtime/config:
- `.env.example`
- `src/drjavanbot/ai/models.py`
- `src/drjavanbot/ai/telemetry.py`
- `src/drjavanbot/data/dental_concepts.json`
- `src/drjavanbot/intelligence/answerability.py`
- `cache.py`, `conversation.py`, `current_provider.py`, `eval.py`, `facets.py`, `fusion.py`, `grounding.py`, `model_policy.py`, `models.py`, `orchestration.py`, `planning.py`, `query_generation.py`, `routing.py`, `scientific_provider.py`, `service.py`, `synthesis.py`, `understanding.py`
- Telegram runtime: `app_v3.py`, `progress.py`, `rendering.py`, `services.py`, `state.py`

Tests:
- Stage 2 conversation/current/fusion/grounding/orchestration/rendering/scientific/cache/runtime/policy/retry tests
- updated QI, routing, requested-fact, model-policy and legacy UI/progress regression tests

Documentation:
- `README.md`, `docs/ai-pipeline.md`, `docs/answer-policy.md`
- `docs/intelligence-v2/{ARCHITECTURE,SOURCE_POLICY,ANSWER_POLICY,EVALUATION,OPERATIONS,FINAL_IMPLEMENTATION_REPORT,STAGE2_CODE_HANDOFF}.md`

## Known limitations

- public Current/Official pages and PubMed can be temporarily unavailable; required-source failure remains fail-closed;
- PubMed abstract/metadata is not full-text systematic appraisal;
- no provenance-backed Curated Dental Knowledge dataset is enabled;
- deterministic archive answers are bounded by what the archive actually contains;
- live LLM/network latency can vary; the final reliability matrix satisfies the Prompt 1 gates but cannot guarantee external provider latency;
- deployment/flag enablement/post-deploy health remain strictly Prompt 2 work.

## Prompt 2 deployment checklist

1. Re-read this handoff and verify GitHub branch tip/tree.
2. Re-verify GitHub `main`; do not assume it is still `222fd6b...`.
3. Verify CI on the exact Stage 2 branch tip.
4. Re-verify production current/service/data integrity and rollback target.
5. Merge only under Prompt 2 policy; no force push.
6. Deploy only through canonical updater/release process; no manual edits in `current`.
7. Enable Intelligence v2 flags only as part of the explicit deployment plan.
8. Run the six post-deploy smokes and inspect telemetry.
9. Roll back to the verified pre-deploy healthy SHA if deployment/post-deploy gates fail.
