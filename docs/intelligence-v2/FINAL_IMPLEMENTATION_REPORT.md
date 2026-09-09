# DrJavanBot Intelligence v2 — Final Verification / Deployment Report

## Final status

**DEPLOY_FAILED_ROLLED_BACK**

Stage 2 passed code, CI, provider, archive and pre-deploy verification and was fast-forwarded to `main`. The canonical updater successfully activated the target release, after which the required full production `RuntimeServices` smoke exposed a code-level integration regression: bare `RCT?` routed to Scientific and returned insufficient evidence instead of using the required Archive fast path. Prompt 2 therefore stopped immediately, disabled the three Intelligence v2 flags and used the canonical rollback path. No production hotfix or product redesign was performed.

## 1. Final architecture

Candidate architecture: Question Intelligence v2 → Source Router v2 → source-specific retrieval → Multi-Source Evidence Fusion → RequestedFactCoverage → compact synthesis → application-owned verbatim supports → Multi-Source Grounding Validator → route-aware renderer.

The compact model schema is `claims[{text,support_ids}]`. Direct answer, claim kind, quotes, citation metadata, source labels, confidence and current-estimate framing are application-owned. Unsupported model memory is not a factual authority.

## 2. Source policy

- Archive: what the DrJavan Telegram archive said; archive-specific questions and terse archive lookups.
- Scientific: PubMed/peer-reviewed dental evidence.
- Current: fresh salary/cost/market facts with geography/date admission.
- Official: authoritative-domain-only current evidence where required.
- Model memory, prompts, queries and conversation context are never factual authorities.
- Dense/vector retrieval remained disabled.

## 3. Model stages / call policy

Candidate policy: deterministic high-confidence Question Intelligence when possible; FAST synthesis with thinking off for simple supported Scientific/Current/Hybrid cases; STRONG only for genuine ambiguity/high-stakes/multi-facet complexity; one structured repair maximum; no blind retry loop. Simple archive e.max and bare `RCT?` were intended to require zero model calls end-to-end.

The production failure demonstrates that the intended zero-call terse-archive invariant was not preserved by the fully integrated `RuntimeServices` path when all Stage 2 flags were enabled.

## 4. Main modules

Stage 2 includes the scientific/current/official providers, source routing and query generation, fusion, requested-fact coverage, compact grounding/synthesis, source-aware caching, model policy, conversation interpretation context, telemetry, route-aware Telegram rendering and the existing root-owned release updater.

## 5. Migration / release details

- repository: `ArianGhsm/DrJavanGroupDatabase`
- Stage 1 base: `730381029a46437f60bdd7a9c4ed8e425970287c`
- verified candidate branch: `feature/intelligence-v2-stage2`
- candidate / merged target SHA: `95aab1b3b3694483bde9cf57035c248cbdced07c`
- candidate tree: `e61231309c25e848845bff40f71f7b351a7fb471`
- pre-merge main / pre-deploy healthy production SHA: `222fd6b952b63613400df2040007ace3af798a0c`
- merge method: fast-forward, no merge commit, no force push
- final `main` after merge: `95aab1b3b3694483bde9cf57035c248cbdced07c`
- failed deployment target: `95aab1b3b3694483bde9cf57035c248cbdced07c`
- restored production / rollback SHA: `222fd6b952b63613400df2040007ace3af798a0c`

The deployment report is committed on the dedicated documentation branch `release/intelligence-v2-deploy-report` so the failed `main` code state is recorded without creating a new deployable `main` SHA.

## 6. Full tests / CI

Exact remote candidate clean checkout:

- `compileall`: PASS
- full pytest: **322/322 passed**
- targeted Stage 2 gates: **45/45 passed**
- GitHub workflow: `DrJavanBot tests`, run `34339733825`, exact SHA `95aab1b3...`: **success**
- CI locked dependencies, compile, FTS5, env compatibility, full pytest, Full Archive Quality Lab and systemd verification: all success

## 7. 125-case Intelligence Lab

- cases: **125**
- required-facet recall: **125/125**
- exact facet set: **125/125**
- primary routing: **125/125**
- archive-specific intent: **125/125**
- Oral Pathology cases: **13**
- distinct facets: **35**
- failures: **0**

## 8. Frozen Archive Quality Lab

Prompt 2 reran the lab against the production archive source with an isolated temporary SQLite index:

- archive files: **247**
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
- grounding verifier accuracy: **1.0000**
- index build: **297.985 s**
- median retrieval: **1264.466 ms**
- p95 retrieval: **1431.803 ms**

The production DB hash was unchanged before/after this isolated lab.

## 9. Before / after comparison

Before rollout, production was healthy on `222fd6b...` with 249,907 archive messages / 249,907 FTS rows and all Stage 2 flags absent. Candidate-only and direct multi-source pre-merge smokes satisfied the intended routing. After canonical activation and enabling all three flags, Scientific and Current cases remained grounded, but the fully integrated runtime changed terse `RCT?` from the intended Archive fast path to Scientific/insufficient. The failure was integration-level and was not detected by the existing unit/CI/direct-service gates.

## 10. Cyst result

Production full `RuntimeServices` with Stage 2 flags enabled:

- mode: Scientific
- supported: yes
- AI calls: **1**
- evidence: **2 Scientific items**
- PubMed support: yes (`PMID:34560739`, `PMID:34469012`)
- Scientific presentation label: correct
- measured latency: **5304.62 ms**

Cyst acceptance passed.

## 11. Salary result

Production full `RuntimeServices` with Stage 2 flags enabled:

- mode: Current
- supported: yes
- AI calls: **1**
- authority: Current Web only
- direct answer contained data year **1405**, numeric range **40–90** and monetary unit
- EvidenceItem publication year/timestamp: **2026 / 2026-01-01**
- Current presentation label: correct
- measured latency: **2406.93 ms**

Salary acceptance passed; no archive authority or model-memory amount was used.

## 12. Archive / Hybrid / RCT / no-evidence

### Archive e.max

Production full runtime remained Archive-grounded with 3 archive citations and correct Archive label, but used **1 AI call** and measured **10297.59 ms**. This is worse than the handoff's intended zero-call simple Archive path and was an early integration warning.

### Hybrid e.max

Pre-merge direct multi-source smoke passed with Archive + Scientific evidence, one synthesis call and both source classes. The production full-runtime Hybrid smoke was not continued after the mandatory RCT failure; Prompt 2 requires immediate stop/rollback rather than continuing a failed acceptance run.

### `RCT?`

**FAILED production acceptance.** Full `RuntimeServices` with all Stage 2 flags enabled returned:

- mode: **Scientific**
- insufficient evidence: **true**
- AI calls: **1**
- Archive citations: **0**
- measured latency: **16842.85 ms**

Required behavior was Archive fast path with no unnecessary Scientific/Current routing. This failure triggered rollback.

### No-evidence sentinel

Production full runtime returned insufficient evidence with **0 AI calls**, no citations/evidence and no hallucinated completion. Measured latency was **18173.54 ms**. The zero-call fail-closed invariant passed.

## 13. Latency

Final pre-deploy repeated candidate reliability matrix from Prompt 1: p50 **3067.92 ms**, p95 **6608.33 ms**. Prompt 2 pre-merge Frozen Archive retrieval: median **1264.466 ms**, p95 **1431.803 ms**.

The production reliability sample was deliberately aborted on the RCT acceptance failure, so no statistically meaningful production p50/p95 is claimed. Individual full-runtime production timings are reported above.

## 14. Structured success / repair rate

Candidate repeated reliability gate: **22/22 successful**, structured failures **0**, repair runs **0**. Structured success was therefore 100% in that candidate gate and repair rate 0%. The production failure was a routing/integration failure, not a structured-output failure.

## 15. AI calls

Candidate repeated reliability gate: 11 model-call rows across 22 questions, average **0.50 model calls/question**. Intended 0-call Archive/RCT/no-evidence behavior held in the direct candidate gate but did not fully hold in the integrated production RuntimeServices path.

## 16. Tokens / question

Candidate 22-question gate totals: input **17,241**, output **3,040**. Averaged over all 22 questions: approximately **783.7 input tokens/question** and **138.2 output tokens/question**. Among the 11 model-call questions: approximately **1567.4 input** and **276.4 output tokens/model-call question**.

## 17. Cost / question

Candidate gate reported total cost: **829.20 IRT**. Average across all 22 questions: approximately **37.69 IRT/question**; average across the 11 model-call questions: approximately **75.38 IRT/model-call question**.

## 18. Final branch SHA

Verified Stage 2 candidate branch SHA used for merge/deploy: `95aab1b3b3694483bde9cf57035c248cbdced07c`. The deployment-report documentation commit is intentionally separate and does not redefine the failed candidate SHA.

## 19. Final main SHA

`95aab1b3b3694483bde9cf57035c248cbdced07c`.

`main` is **not** force-reset after the failed rollout. A new coding-phase fix is required before another deployment attempt.

## 20. Production SHA

After rollback: `222fd6b952b63613400df2040007ace3af798a0c`.

## 21. Production health after rollback

- `drjavanbot.service`: active/running
- MainPID after rollback verification: `28673`
- restart count: **0**
- updater path: active/waiting
- updater oneshot: inactive/dead with successful result, normal idle state
- Telegram smoke/getMe: healthy, bot id `8926046613`
- AvalAI via `RuntimeServices`: `ai_configured=true`, API validation **true**
- archive index `quick_check`: **ok**
- messages: **249,907**
- FTS rows: **249,907**
- cache/data/secrets directories accessible with expected service ownership/permissions
- recent post-rollback journal error scan: no repeated exception loop

## 22. Rollback

- previous healthy / restored SHA: `222fd6b952b63613400df2040007ace3af798a0c`
- update request: `9c45ec943ae91a24`, updater result `success`, change class `index`, duration **333.101 s**
- rollback request: `12d05e8b1dd4295e`, updater result `rolled_back`, duration **303.261 s**
- `.deploy_commit` restored to rollback SHA
- canonical rollback rebuilt and promoted a compatible archive DB
- archive/FTS counts remained **249,907 / 249,907**
- raw archive content comparison between previous and Stage 2 releases: **247/247 files, 0 byte-content mismatches**

The physical SQLite SHA-256 changed across canonical reindex/promotion (`87b60a...` pre-deploy, `a7a244...` after Stage 2 activation, `31a3be...` after rollback). This is expected for rebuilt SQLite files; logical counts and integrity checks remained stable.

## 23. Feature flags

Stage 2 rollout enabled:

- `DRJAVAN_INTELLIGENCE_V2=true`
- `DRJAVAN_SOURCE_ROUTER_V2=true`
- `DRJAVAN_HYBRID_RETRIEVAL_V2=true`

After the failed production smoke, all three were atomically set to **false** before canonical rollback. Current production state after rollback: all three **false**. No dense/vector flag was enabled.

## 24. Known limitations / blocking bug

Blocking bug for the next coding phase: the fully integrated production `RuntimeServices` path with all Stage 2 flags enabled does not preserve the deterministic terse Archive routing contract. `RCT?` became Scientific/insufficient with one AI call, and simple Archive e.max also incurred one AI call. Direct-service/pre-merge gates did not expose this integration behavior.

The exact root cause must be diagnosed in the coding phase. Prompt 2 did not change Question Intelligence, routing, prompts, grounding or tests to work around it.

External PubMed/current providers also remain subject to bounded network availability and latency, as documented in the Stage 2 handoff.

## 25. Required future improvements

Return to Prompt 1 / a new coding-phase branch. Add exact full-`RuntimeServices` acceptance tests with the three Stage 2 flags enabled for all six canonical questions, including explicit assertions for `RCT?` Archive routing, simple Archive zero-call behavior and no-evidence zero-call behavior. Diagnose why integrated Question Intelligence changes the deterministic terse route; fix it in code, rerun the complete 125-case/Quality-Lab/live/repeated gates, then issue a new deploy candidate. Do not reuse `95aab1b3...` as a production-ready candidate.

## Acceptance verdict

The Stage 2 target passed merge, CI and canonical updater activation but failed a required post-deploy product-runtime gate. Production was restored to the prior healthy release with flags disabled.

**FINAL STATUS: DEPLOY_FAILED_ROLLED_BACK**
