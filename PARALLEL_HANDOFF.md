# PARALLEL HANDOFF — Hybrid Discussion Retrieval Engine v2

## Branch / baseline

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Branch: `parallel/brain-retrieval-engine-v2`
- Locked base / merge base: `4226f2c30a04d89a28afe8284be5c3e5f657a2a6`
- Final implementation + regression-test HEAD before this handoff-only documentation commit: `11d4f10e2681c3eae3131421936a6e20e7654e66`
- The exact branch-tip SHA containing this file is reported in the final chat handoff. A commit cannot embed its own SHA in its own contents without changing that SHA.
- `main` was not modified or merged. No newer `main` or parallel branch was merged/rebased.
- Raw archive `گروه دکتر جوان/messages*.html` is untouched.

## Design rationale

The previous retrieval path fused and ranked individual messages, then treated family hits within a broad same-page neighborhood as a conversation bridge. That allowed a generic facet hit such as age/year to improve a topic hit even when the evidence did not form one coherent discussion. Context hydration also happened only after message ranking, so reply/neighbor evidence could not materially establish discussion-level facet coverage.

This branch replaces that message-level bridge with a bounded Hybrid Discussion Retrieval Engine while keeping `SearchPlan` backward-compatible and preserving the existing SQLite/FTS substrate.

Pipeline:

1. **Bounded per-family candidate generation** using the existing SQLite FTS/exact/token/fuzzy backend.
2. **Deterministic per-message fusion** with normalized local lexical score plus reciprocal-rank contribution.
3. **Topic-anchor qualification** from `SearchPlan` core concepts and topic/product/material/entity families. Bounded corpus/refinement families may recover a missing topic anchor; generic facet rescue families do not become anchors.
4. **Conversation graph / discussion clustering** using direct reply links, cross-page reply relations, and bounded same-page chronological proximity around topic anchors.
5. **Facet co-location** computed only inside one discussion. A topic in discussion A and age/quality/etc. in discussion B does not count as complete coverage.
6. **Discussion-level reranking** using interpretable features: topic anchor, qualified family/facet coverage, required-facet completeness, exact anchor, reply/proximity edges, bounded author diversity, correction cues, span penalty, and duplicate/neighborhood control.
7. **Adaptive context hydration** only for top discussions, with wider windows for facet-complete/topic-rich clusters and smaller windows for weak/unanchored hits.
8. **Diversified evidence extraction** from discussion representatives so one author/thread does not monopolize top-K.
9. **Quality-aware stopping state** exported for later orchestrator decisions.

Hard bounds currently used:

- topic/facet chronological bridge distance: `8` messages
- anchor merge distance: `6` messages
- maximum discussion candidates: `140`
- maximum members per discussion: `20`
- maximum hydrated discussions in the normal retrieval path: `12`
- maximum packed context records per hydrated discussion: `20`
- backend fuzzy behavior remains the existing bounded implementation; no unbounded scan was added.

## Ranking / evidence semantics

`DiscussionCandidate` aggregates:

- direct topic hits
- qualified query-family hits
- requested-facet coverage
- direct reply and chronological proximity edges
- author count
- source span
- context dependency
- correction/disagreement cue count
- cluster coherence signals

Backward-compatible reason aliases (`anchor_family_hit`, `conversation_bridge`, `reply_context`) are retained for existing callers. Their semantics are stricter: `conversation_bridge` now means a facet is genuinely co-located in a topic-anchored discussion, not merely present elsewhere in a broad neighborhood.

Compound query-family coverage is qualified. A multi-token family such as a topic + experience phrase cannot claim coverage from an FTS OR result that contains only one generic token.

Mixed Latin product forms gain only orthographic search variants. Example: normalized `e max` can additionally search compact `emax`. No clinical answer dictionary or answer-specific facts/message IDs were introduced.

## Quality-aware stopping contract

`RetrievalReport.quality_state` can report:

- `strong_direct_answer_candidate`
- `facet_complete_discussion`
- `only_topical_facet_missing`
- `generic_noisy_coverage`
- `topical_coverage`
- `no_candidates`

Existing `assess_planned_retrieval()` remains callable. It now understands multi-author evidence collapsed into one discussion and retains a bounded legacy stop for dense single-family evidence, while small fallback clusters remain refinement-eligible.

## RetrievalReport contract changes

Old fields remain available. Added bounded fields:

- `discussion_count`
- `facet_complete_discussions`
- `topic_anchored_bridges`
- `hydrated_discussions`
- `duplicates_suppressed`
- `ranking_reason_counts`
- `quality_state`
- `families_executed`
- `topic_anchored_discussions`
- `required_facet_groups_total`
- `max_required_facet_groups_hit`

`ranking_reason_counts` contains bounded reason labels/counts only; raw message text is not emitted as telemetry.

`context_hydrated` and `discussion_windows` remain compatibility counters expressed as covered candidate/member anchors after clustering. `hydrated_discussions` is the explicit v2 discussion-level hydration count.

## Files changed / added

Core retrieval:

- `src/drjavanbot/ai/retrieval.py`
- `src/drjavanbot/ai/retrieval_contracts.py`
- `src/drjavanbot/ai/discussion_types.py`
- `src/drjavanbot/ai/discussion_query.py`
- `src/drjavanbot/ai/discussion_facets.py`
- `src/drjavanbot/ai/discussion_links.py`
- `src/drjavanbot/ai/discussion_cluster.py`
- `src/drjavanbot/ai/discussion_features.py`
- `src/drjavanbot/ai/discussion_score.py`
- `src/drjavanbot/ai/discussion_hydrate.py`
- `src/drjavanbot/ai/discussion_select.py`

Benchmark / tests:

- `src/drjavanbot/semantic_benchmark.py`
- `tests/test_semantic_benchmark.py`
- `tests/test_stage13_discussion_retrieval.py`
- `tests/test_stage13_discussion_corrections.py`

Intentionally not changed by this branch:

- `src/drjavanbot/ai/planner.py`
- `src/drjavanbot/ai/orchestrator.py`
- `src/drjavanbot/ai/evidence.py`
- `src/drjavanbot/ai/validation.py`
- synthesis prompts
- SQLite schema/index definitions
- dependency lock files
- raw archive HTML

## Targeted regression coverage

New synthetic/contract regressions cover:

- required facets in unrelated windows do **not** co-locate
- short answer/reply can attach across archive pages through reply graph
- generic facet pollution is suppressed when no topic anchor exists
- `e.max` / `e max` / `emax` orthographic recall remains bounded
- context hydration is capped to top discussions
- partial OR lexical hits cannot fake compound facet coverage
- bounded ranking telemetry contains reasons/counts, not raw topic text
- correction/disagreement cues are detected as bounded discussion metadata without raw-text telemetry

The full real-archive semantic suite covers:

- pediatric orthodontic timing
- composite recommendation
- direct e.max lookup
- Persian question / English e.max form
- typo zirconia
- e.max vs zirconia
- short RCT query
- generic facet pollution sentinel
- low-information noise
- no-evidence sentinel

## Test / CI results

Last fully green implementation CI before the handoff-only/test-tail commits:

- workflow run: `34186310646`
- implementation HEAD: `e9386606f384042d5ea2bb89d99e7abd56c255ee`
- compile: pass
- SQLite FTS5 verification: pass
- full pytest: **175 passed in 7.59s**
- full archive semantic benchmark (`--strict --top-k 12`): **pass, zero strict failures**
- systemd verification: pass

A final CI run on the branch tip containing this handoff and the additional correction-cue regression is required/verified before the final chat response; production retrieval code is unchanged from the green benchmarked implementation.

## Full-archive benchmark: baseline -> v2

Canonical archive was unchanged in both runs: **247 files, 249,907 messages**.

| Metric | Baseline `4226f2c` | Discussion v2 `e9386606` | Delta |
|---|---:|---:|---:|
| Strict benchmark | pass | pass | non-regression |
| DB size | 373,555,200 B | 373,555,200 B | **0 B** |
| Median retrieval latency | 2768.929 ms | 1631.017 ms | **-1137.912 ms (-41.1%)** |
| Common-case p95* | 2996.471 ms | 2178.305 ms | **-818.166 ms (-27.3%)** |
| Pediatric top-12 topical relevance | 4/12 (33.33%) | **12/12 (100%)** | **+66.67 pp** |
| Pediatric irrelevant rate | 66.67% | **0%** | **-66.67 pp** |
| Pediatric latency | 2945.039 ms | 2178.305 ms | **-766.734 ms (-26.0%)** |
| Pediatric independent authors top-12 | 9 | **12** | +3 |
| Pediatric query runs | 18 | 18 | unchanged |

\* Baseline schema did not expose p95. The baseline p95 is reconstructed with the same nearest-rank rule from the per-case latencies printed by the locked-base CI. The v2 suite also includes the new generic-facet sentinel, so this p95 is a useful performance comparison but not a perfectly identical-case statistic.

Index build happened to measure 277.20 s baseline vs 231.46 s in the v2 run, but this branch did not change indexing/schema; that difference is treated as runner/environment noise, not a claimed optimization.

## Pediatric orthodontic timing regression

Baseline:

- relevant top-12: `4/12`
- irrelevant candidate rate: `0.6667`
- required facet groups: globally `2/2`, colocated `2/2`
- conversation bridges: `45` under the old broad-neighborhood semantics
- independent authors: `9`
- latency: `2945.039 ms`

Discussion v2:

- relevant top-12: **`12/12`**
- irrelevant candidate rate: **`0.0`**
- first relevant discussion rank: **1**
- reciprocal rank proxy: **1.0**
- required facet groups: `2/2`
- max colocated required groups: **`2/2`**
- facet-complete discussions: `36`
- topic-anchored discussions: `102`
- independent authors top-12: **12**
- independent threads top-12: **12**
- genuine topic-anchored bridges: `7`
- hydrated discussions: `6`
- latency: **`2178.305 ms`**
- quality state: **`facet_complete_discussion`**

The bridge count is intentionally lower because the metric is now stricter and discussion-local. The old 45 and new 7 are not semantically identical counters.

The old message-level anchor-recall proxy drops from `0.0145` to `0.0048` for this case because v2 emits one representative per discussion rather than many raw anchor messages. This is disclosed rather than hidden. The new discussion metrics, first relevant rank, top-K topic relevance, co-location, independent threads and explicit discussion counts better describe the new retrieval unit. The current `discussion_recall_proxy` is `0.0116` against a bounded source-window proxy pool, not manually adjudicated relevance gold.

## Other real-archive results

| Case | Baseline latency | v2 latency | Topical result | v2 first rank / RR |
|---|---:|---:|---|---|
| Composite recommendation | 2996.471 ms | 1973.353 ms | 12/12 -> 12/12 | 1 / 1.0 |
| e.max direct | 2774.185 ms | 1608.166 ms | 12/12 -> 12/12 | 1 / 1.0 |
| Persian e.max experience | 2766.438 ms | 1653.868 ms | 12/12 -> 12/12 | 1 / 1.0 |
| e.max vs zirconia | 2768.929 ms | 1747.104 ms | 12/12 -> 12/12 | 1 / 1.0 |
| RCT | 2992.087 ms | 1741.534 ms | 12/12 -> 12/12 | 1 / 1.0 |
| typo zirconia | 244.448 ms | 278.590 ms | 1/1 -> 1/1 | 1 / 1.0 |

The typo-only case regressed by ~34 ms (+14.0%) in absolute latency while preserving recall. This is small in absolute terms but remains an explicit performance risk/optimization opportunity.

The new generic-facet pollution sentinel executes real high-frequency age/timing searches but returns **0 final evidence candidates** when its topic anchor is absent. This demonstrates that raw broad hits are not accepted as answer evidence.

## Semantic-layer decision

No external embedding API, vector database, heavyweight embedding dependency, or new semantic index was added. The lexical + bounded conversation-graph approach produced a large precision and latency gain on the required archive regression without increasing DB size, so an additional semantic dependency was not justified by this benchmark.

## Index / migration impact

- SQLite schema changes: **none**
- index format changes: **none**
- migration required: **no**
- external service required: **no**
- new dependency: **no**
- benchmark DB size delta: **0 bytes**

## Risks / unresolved items

1. `Discussion Recall@K` is currently represented by a bounded source-window proxy, not a manually adjudicated discussion relevance set. A small human-labelled gold set would make recall tuning substantially more meaningful.
2. Because one discussion now emits one representative, old message-level anchor recall is not directly comparable and can decrease even while top-K discussion precision improves materially.
3. High-volume topics can hit the `MAX_DISCUSSIONS=140` cap (pediatric case does). Top-12 precision is currently 100%, but future labelled recall testing should verify whether the cap can be lowered or needs adaptive expansion.
4. The typo-only zirconia case has a small absolute latency regression (~34 ms).
5. Required-facet mapping consumes `SearchPlan.required_aspects` and family/query vocabulary. If the parallel planner branch changes family naming semantics, rerun stage13 targeted tests and the full archive benchmark before integration.
6. Correction/disagreement detection is deliberately conservative token-cue metadata; it does not assert which message is clinically correct.

## Integration dependencies / likely conflicts

- `src/drjavanbot/ai/retrieval.py` is the primary expected conflict hotspot with other brain branches.
- Current `SearchPlan` is consumed unchanged; no planner contract migration is required by this branch.
- `RetrievalReport` implementation moved to `retrieval_contracts.py` but remains imported/re-exported from `ai.retrieval` for existing callers.
- Existing match-reason aliases are retained for orchestrator/evidence compatibility.
- `semantic_benchmark.py` output schema is extended; consumers that strictly validate its JSON fields should accept the additional fields.
- No changes were made to planner, orchestrator, evidence packer, validator, synthesis prompts, owner/security/runtime/update code.

## Safety / archive integrity

- No hard-coded answer facts.
- No hard-coded archive message IDs for production ranking.
- No model memory used as evidence.
- No raw archive HTML edits.
- No runtime DB/session/secret/log/backup artifacts committed.
- No unbounded fuzzy or context scan added.
- No merge to `main`.
- **Raw archive untouched.**
