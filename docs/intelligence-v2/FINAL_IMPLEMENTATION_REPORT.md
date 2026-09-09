# DrJavanBot Intelligence v2 — Final Implementation & Production Report

## Final status

`PRODUCTION_HEALTHY`

Intelligence v2 Stage 2 is merged and running in canonical production. A first rollout of candidate `95aab1b3b3694483bde9cf57035c248cbdced07c` was correctly rolled back after full `RuntimeServices` smoke exposed a deterministic-routing integration bug. The bug was fixed in PR #12 and the corrected release was deployed only after local, PR and exact-main CI gates were green.

## Final architecture

Question Intelligence → Source Router → source-specific retrieval → Evidence Fusion → RequestedFactCoverage → compact synthesis (`claims[{text,support_ids}]`) → application-owned verbatim support spans → Multi-Source Grounding Validator → route-aware renderer.

Model memory is not factual authority. Archive, Scientific and Current claims are authorized only by their corresponding EvidenceItems. Hybrid answers keep authority classes separate. Dense/vector retrieval remains disabled.

## Source / grounding policy

- Archive authority: group archive only.
- Scientific authority: scientific provider/PubMed evidence.
- Current authority: current web/official evidence.
- Unknown support IDs, fabricated quotes, missing required sources and unsupported numeric claims fail closed.
- Salary/current answers require evidence-backed amount/range, monetary unit and data year/date.
- No-evidence stops before synthesis.
- One structured repair maximum; no unbounded retry loop.

## Runtime integration bug and fix

The failed first rollout showed `RCT؟` becoming `mixed-language` because Persian punctuation was counted as a Persian letter. Medium-confidence mixed-script queries could then unnecessarily invoke Question Intelligence, allowing `RCT؟` to drift from deterministic Archive routing to Scientific. Simple archive e.max also paid an unnecessary QI call.

Fix PR #12 (`e90172f92e6b13368b884e0a0e4cedb08b9ce157`):

- Arabic-block punctuation/digits are no longer language-bearing characters.
- Mixed script alone no longer escalates medium-confidence deterministic dental semantics to QI; genuine low-confidence/ambiguous/high-stakes/multifacet cases still may escalate.
- Full `RuntimeServices` regression coverage was added with all three v2 flags enabled for the six canonical deployment questions.
- `RCT؟` and simple archive e.max explicitly assert zero QI calls.

No retrieval architecture, grounding semantics, provider or synthesis redesign was included in this fix.

## Git / CI

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Stage 1 base: `730381029a46437f60bdd7a9c4ed8e425970287c`
- Original Stage 2 candidate: `95aab1b3b3694483bde9cf57035c248cbdced07c`
- Final fix branch: `fix/intelligence-v2-runtime-integration`
- Final fix/main SHA: `e90172f92e6b13368b884e0a0e4cedb08b9ce157`
- Final tree: `59a97478e8dc2f867c06503873ba5320a12ee071`
- Merge method: fast-forward; no force push; PR #12 is recorded merged with the same SHA.
- PR CI run #249: success.
- Exact-main CI run #250 (`34347363118`): success, including locked dependencies, compile, FTS5, env compatibility, full pytest, Full Archive Quality Lab and systemd verification.

## Test / evaluation gates

- Compile: PASS.
- Full pytest on corrected candidate: 326/326 PASS.
- Focused runtime/QI regression: 17/17 PASS.
- Production rendering/runtime targeted tests: 4/4 PASS.
- Intelligence Lab: 125 cases; required-facet recall 125/125; routing 125/125; archive intent 125/125; 13 Oral Pathology cases; 35 distinct facets. A simple exact-tuple helper measured 117/125 both before and after the runtime fix, so the fix introduced no facet delta.
- Frozen Archive Quality Lab: 37 cases, 0 strict failures; Recall@12/MRR/topic relevance/facet colocation = 1.0000; false-insufficient = 0; false-supported = 0; grounding verifier = 1.0000.

## Canonical deployment

Final canonical updater request: `1d7bc6b463c2b0df`.

- Previous healthy/rollback SHA: `222fd6b952b63613400df2040007ace3af798a0c`.
- Target: `e90172f92e6b13368b884e0a0e4cedb08b9ce157`.
- Updater CI status: success.
- Change class: `index`.
- Staging index was built/validated before atomic switch.
- Updater terminal state: `success`.
- Updater duration: 330.465 s.
- `/opt/drjavanbot/current` and `.deploy_commit` both point to the final SHA.

Feature flags in the live process:

- `DRJAVAN_INTELLIGENCE_V2=true`
- `DRJAVAN_SOURCE_ROUTER_V2=true`
- `DRJAVAN_HYBRID_RETRIEVAL_V2=true`

No dense/vector flag was enabled.

## Production smoke

All six full-production `RuntimeServices` cases passed:

1. Odontogenic cyst prevalence → Scientific, supported, PubMed/scientific evidence, 1 AI call.
2. New-graduate dentist salary in Iran → Current, current-web authority, year `1405` / source year 2026, monetary range/unit present, 1 AI call.
3. Group e.max → Archive only, archive citations, 0 AI calls.
4. Group e.max + scientific evidence → Hybrid, both Archive and Scientific evidence classes, 1 AI call.
5. `RCT؟` → Archive fast path, archive citations, 0 AI calls.
6. No-evidence sentinel → insufficient, no hallucinated completion, 0 AI calls.

Route-aware labels are present in production rendering: `🗂` Archive, `📚` Scientific, `🌐` Current, with Hybrid separated as multi-source output.

## Production reliability sample

Fresh-cache bounded production sample: 14/14 accepted.

- Archive e.max: 3/3, zero AI calls.
- Hybrid e.max: 3/3, Archive + Scientific, one AI call each.
- Cyst: 2/2, Scientific, one AI call each.
- Salary: 2/2, Current/evidence-backed, one AI call each.
- RCT: 2/2, Archive, zero AI calls.
- No-evidence: 2/2, insufficient, zero AI calls.
- Invalid citations: 0.
- Grounding/support quote failures: 0.
- Structured repairs: 0.
- Telemetry failures: 0.
- AI calls: 7 across 14 runs.
- Input tokens: 11,119.
- Output tokens: 1,863.
- Reported cost: 421.43 IRT total (~30.10 IRT/question).
- p50 latency: 2.997 s.
- p95 latency: 20.629 s. One hybrid run was the latency outlier but remained fully supported and valid.

## Production health / integrity

- Production SHA: `e90172f92e6b13368b884e0a0e4cedb08b9ce157`.
- Bot service: active/running; stable PID after rollout; `NRestarts=0`.
- Updater path: active; updater one-shot idle/dead with `Result=success` after completion.
- Telegram `getMe`: healthy; bot ID verified without exposing token.
- AvalAI: configured and API validation successful through `RuntimeServices`; no secret value printed.
- Archive DB `quick_check=ok`.
- Messages: 249,907.
- FTS rows: 249,907.
- Raw archive: 247 files in previous and final release; 0 byte-content mismatches.
- Recent repeated exception/error loop: none.
- Disk: healthy (~27 GB free at final check).

The SQLite physical hash changed during updater-managed reindex, which is expected; logical cardinality/integrity and raw archive bytes remained unchanged.

## Rollback readiness

Immediate rollback candidate remains `222fd6b952b63613400df2040007ace3af798a0c`. Its release/venv/deploy marker was previously verified and canonical rollback was exercised successfully during the first failed rollout. Architecture rollback is available by disabling the three Intelligence v2 flags and restarting through the documented service procedure.

## Known limitations / future improvements

- Live hybrid latency can still vary materially with external scientific/provider response time; the 14-run sample had one ~20.6 s hybrid outlier.
- Dense/vector retrieval remains intentionally disabled until a benchmark demonstrates a worthwhile benefit.
- The generic CLI `health` command does not infer server-only AI SecretStore configuration unless invoked through the full runtime context; operational AI health should use `RuntimeServices`/provider validation.
- Future regression work should keep the six canonical questions as full-runtime tests, not only direct service tests.
