# DrJavanBot Intelligence v2 — Stage 2 Code Stabilization Report

## Status

Prompt 1 stabilizes Stage 2 code only. **No merge to `main`, no production deploy, no production restart and no production feature-flag mutation were performed.**

## Architecture result

Question Intelligence → Source Router → source-specific retrieval → Evidence Fusion → RequestedFactCoverage → compact synthesis → application-owned verbatim supports → Multi-Source Grounding Validator → route-aware renderer.

The final synthesis schema is intentionally small: `claims[{text,support_ids}]`. Direct answer, claim kind, citation metadata, quotes, confidence and current-estimate framing are derived locally. Simple archive questions can bypass the model entirely. Model memory is never factual authority.

## Model/call policy

- deterministic QI: 0 calls in the normal fast path
- simple archive e.max / `RCT?`: 0 synthesis calls
- supported scientific/current/hybrid: target 1 synthesis call
- one repair maximum; no blind/unbounded retries
- hybrid alone does not force STRONG
- FAST live gate: `deepseek-v4-flash`, thinking disabled

## Grounding hardening

- model selects opaque support IDs; it never authors quote text
- support excerpts are contiguous application-created spans from real EvidenceItems
- unknown IDs / wrong source / missing required source fail closed
- every claim number must exist in validated support
- local numeric span attachment is limited to the same EvidenceItem **and matching support context**
- salary/cost requires supported amount/range + currency/unit + data year/date
- no-evidence sentinel stops before synthesis
- output truncation and two failed structured attempts return insufficient evidence

## Provider reliability

PubMed uses bounded E-utility calls with process-wide pacing and one retry for transient 408/429/5xx/network timeout classes. Current salary admission requires real monetary evidence; full bounded page extraction preserves contiguous monetary windows. SSRF/private-network rejection remains in place.

## Evaluation

- Intelligence Lab: 125/125 required-facet recall, exact facet set, routing and archive intent; 13 Oral Pathology; 35 facets
- repeated live reliability: 22/22; 0 failures; 0 repairs; 0 structured failures
- live p50 3.068 s; p95 6.608 s
- input/output tokens 17,241 / 3,040; reported cost 829.20 IRT
- cache hit: 0 synthesis calls
- Frozen Archive Quality Lab: 37 cases, 0 strict failures, Recall@12/MRR/topic/facet = 1.0000; grounding 12/12; scripted 5/5

## Dense retrieval

Disabled. No local multilingual A/B benchmark established a benefit sufficient to justify RAM/latency/privacy cost.

## Production preflight/integrity

Observed production SHA and rollback target: `222fd6b952b63613400df2040007ace3af798a0c`. Service remained active with the same PID and zero restarts during final integrity check. All three Intelligence v2 flags remained absent. Production archive DB SHA-256 remained `87b60aeb6d1931ee41dec294731d7b0866f48213b6d8621ed77d32103a11a4a6`. The 247-file raw archive manifest remained byte-identical with SHA-256 `0b7fa86c1b402a5e2ec66878f5ba06a33d7ef88744c2e710cdd937cb56cfe560`.

## Git

- repository: `ArianGhsm/DrJavanGroupDatabase`
- Stage 1 base: `730381029a46437f60bdd7a9c4ed8e425970287c`
- branch: `feature/intelligence-v2-stage2`
- code stabilization SHA: `c7ff5d00ba9cc0521b3f30894eaff3305fa8bf36`
- code tree: `cb0e429b57d88ea1563e2f3c763295748d403081`
- observed `main` / production pre-deploy SHA: `222fd6b952b63613400df2040007ace3af798a0c`
- final documentation/push SHA is verified after the documentation commit and recorded in the operator handoff/final response.

## Remaining deployment work

Prompt 2 must re-verify GitHub `main`, CI, production current/rollback state, deploy through the canonical updater, enable flags only under the deployment plan, run post-deploy smokes and retain rollback capability.
