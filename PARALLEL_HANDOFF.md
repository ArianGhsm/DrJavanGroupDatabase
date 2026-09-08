# PARALLEL HANDOFF — Hybrid Discussion Retrieval Engine v2

## Branch / baseline

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Branch: `parallel/brain-retrieval-engine-v2`
- Locked base / merge base: `4226f2c30a04d89a28afe8284be5c3e5f657a2a6`
- Final implementation + regression-test HEAD before handoff documentation: `11d4f10e2681c3eae3131421936a6e20e7654e66`
- Fully verified handoff-containing branch tip before this documentation-only metadata update: `f1b4dd4d2cc7c85851226e5452b4440104c9d4bf`
- Exact final branch-tip SHA after this documentation-only commit is reported in the final chat. A Git commit cannot embed its own SHA in its own contents without changing that SHA.
- `main` was not modified or merged. No newer `main` or parallel branch was merged/rebased.
- Raw archive `گروه دکتر جوان/messages*.html` is untouched.

## Design rationale

The baseline fused/ranked individual messages and treated query-family hits inside a broad same-page neighborhood as conversation coverage. This could let generic facets such as age/year raise a topic hit even when topic and facet were not one coherent discussion. Context was hydrated after message ranking, so reply/neighbor evidence did not establish discussion-level facet coverage.

This branch converts that path to a bounded Hybrid Discussion Retrieval Engine while preserving the current SQLite/FTS substrate and consuming `SearchPlan` backward-compatibly.

Pipeline:

1. bounded per-family SQLite lexical/FTS/exact/token/fuzzy candidate generation;
2. deterministic per-message fusion using normalized lexical score plus reciprocal-rank contribution;
3. topic-anchor qualification from core concepts and topic/product/material/entity families;
4. bounded conversation graph via direct replies, cross-page reply links and same-page chronological proximity;
5. discussion clustering around topic anchors;
6. requested-facet co-location inside the same discussion only;
7. interpretable discussion reranking;
8. adaptive hydration only for top discussions;
9. diversified evidence extraction and explicit retrieval quality state.

Hard bounds:

- topic/facet chronological bridge distance: `8` messages
- anchor merge distance: `6` messages
- max discussion candidates: `140`
- max members/discussion: `20`
- max hydrated discussions on normal path: `12`
- max packed context records/discussion: `20`
- no unbounded fuzzy/context scan added.

## Ranking / evidence semantics

`DiscussionCandidate` aggregates direct topic hits, qualified family/facet hits, required-facet coverage, reply/proximity edges, author diversity, source span, context dependency and conservative correction/disagreement cues.

Backward-compatible reasons `anchor_family_hit`, `conversation_bridge`, and `reply_context` remain available. `conversation_bridge` is now stricter: it represents a facet genuinely co-located in a topic-anchored discussion rather than a generic family hit elsewhere in a broad neighborhood.

Compound family coverage is qualified: a multi-token family cannot claim coverage from an FTS OR candidate containing only one generic token.

Mixed Latin product forms receive bounded orthographic variants only; e.g. normalized `e max` may add compact `emax`. No clinical answer dictionary, answer fact or production message ID is hard-coded.

## Quality-aware stopping

`RetrievalReport.quality_state` can report:

- `strong_direct_answer_candidate`
- `facet_complete_discussion`
- `only_topical_facet_missing`
- `generic_noisy_coverage`
- `topical_coverage`
- `no_candidates`

`assess_planned_retrieval()` remains compatible. It understands multi-author evidence collapsed into one discussion, retains the bounded legacy stop for dense single-family evidence, and leaves small fallback clusters refinement-eligible.

## RetrievalReport contract changes

Old fields remain. Added bounded fields:

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

`ranking_reason_counts` contains bounded reason labels/counts only, never raw archive text. Legacy `context_hydrated` / `discussion_windows` remain compatibility counters over covered candidate/member anchors; `hydrated_discussions` is the explicit v2 discussion count.

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

Benchmark/tests:

- `src/drjavanbot/semantic_benchmark.py`
- `tests/test_semantic_benchmark.py`
- `tests/test_stage13_discussion_retrieval.py`
- `tests/test_stage13_discussion_corrections.py`

Intentionally unchanged:

- `ai/planner.py`, `ai/orchestrator.py`, `ai/evidence.py`, `ai/validation.py`
- synthesis prompts
- SQLite schema/index definitions
- dependency locks
- raw archive HTML
- owner/security/runtime/updater code.

## Regression coverage

Targeted tests cover:

- unrelated windows do not fake facet co-location;
- short reply evidence can connect cross-page by reply graph;
- generic facet pollution is suppressed when topic anchor is absent;
- `e.max` / `e max` / `emax` recall remains bounded;
- hydration is capped to top discussions;
- partial OR hits do not fake compound facet coverage;
- ranking telemetry is bounded and excludes raw topic text;
- correction/disagreement cues are bounded metadata without raw-text telemetry.

Full real-archive suite covers pediatric orthodontic timing, composite recommendation, direct e.max, Persian/English e.max, typo zirconia, e.max vs zirconia, RCT, generic facet pollution, low-information noise and no-evidence sentinel.

## Final test / CI verification

Fully verified branch state before this documentation-only metadata commit:

- workflow run: `34186825113`
- verified branch head: `f1b4dd4d2cc7c85851226e5452b4440104c9d4bf`
- compile: pass
- SQLite FTS5: pass
- full pytest: **176 passed in 9.27s**
- full archive semantic benchmark `--strict --top-k 12`: **pass; zero strict failures**
- systemd verification: pass
- benchmark corpus: **247 files / 249,907 messages**
- DB size: **373,555,200 bytes**
- final-run median retrieval latency: **1429.7465 ms**
- final-run p95 retrieval latency: **1958.699 ms**

This metadata update changes only `PARALLEL_HANDOFF.md`; production retrieval code and tests are unchanged. The exact final documentation tip is re-verified by CI before final chat delivery.

## Metric delta: locked baseline -> final verified v2

| Metric | Baseline `4226f2c` | Verified v2 `f1b4dd4` | Delta |
|---|---:|---:|---:|
| Strict benchmark | pass | pass | non-regression |
| DB size | 373,555,200 B | 373,555,200 B | **0 B** |
| Median retrieval latency | 2768.929 ms | 1429.7465 ms | **-1339.1825 ms (-48.4%)** |
| Common-case p95* | 2996.471 ms | 1958.699 ms | **-1037.772 ms (-34.6%)** |
| Pediatric top-12 topic relevance | 4/12 (33.33%) | **12/12 (100%)** | **+66.67 pp** |
| Pediatric irrelevant rate | 66.67% | **0%** | **-66.67 pp** |
| Pediatric latency | 2945.039 ms | 1958.699 ms | **-986.340 ms (-33.5%)** |
| Pediatric independent authors top-12 | 9 | **12** | +3 |
| Pediatric query runs | 18 | 18 | unchanged |

\* Baseline schema did not expose p95; baseline p95 is reconstructed with the same nearest-rank rule from the locked-base CI per-case latencies. v2 also adds the generic-facet sentinel, so the p95 comparison is informative but not an exactly identical-case set.

Index time was 277.20 s baseline and 272.15 s in the final verified run. Index/schema code is unchanged, so this difference is treated as runner noise rather than a claimed optimization.

### Pediatric orthodontic timing

Baseline:

- top-12 relevant `4/12`
- irrelevant rate `0.6667`
- required groups globally/co-located `2/2`
- old broad-neighborhood bridges `45`
- independent authors `9`
- latency `2945.039 ms`

Verified v2:

- top-12 relevant **`12/12`**
- irrelevant rate **`0.0`**
- first relevant discussion rank **1**
- reciprocal-rank proxy **1.0**
- required groups / max co-located **`2/2`**
- facet-complete discussions `36`
- topic-anchored discussions `102`
- independent authors top-12 **12**
- independent threads top-12 **12**
- genuine topic-anchored bridges `7`
- hydrated discussions `6`
- latency **`1958.699 ms`**
- quality state **`facet_complete_discussion`**

The bridge counters are not semantically identical: old `45` used broad proximity; new `7` is stricter discussion-local coverage.

The old message-level anchor-recall proxy falls from `0.0145` to `0.0048` because v2 emits discussion representatives rather than many raw anchor messages. This is intentionally disclosed. New first-rank, top-K relevance, co-location, independent-thread and discussion metrics better reflect the retrieval unit. Current `discussion_recall_proxy=0.0116` uses a bounded source-window proxy pool, not manually adjudicated relevance gold.

### Other real-archive cases

| Case | Baseline latency | Verified v2 latency | Topical result | Rank / RR |
|---|---:|---:|---|---|
| Composite recommendation | 2996.471 ms | 1774.006 ms | 12/12 -> 12/12 | 1 / 1.0 |
| e.max direct | 2774.185 ms | 1414.326 ms | 12/12 -> 12/12 | 1 / 1.0 |
| Persian e.max experience | 2766.438 ms | 1445.167 ms | 12/12 -> 12/12 | 1 / 1.0 |
| e.max vs zirconia | 2768.929 ms | 1538.834 ms | 12/12 -> 12/12 | 1 / 1.0 |
| RCT | 2992.087 ms | 1551.087 ms | 12/12 -> 12/12 | 1 / 1.0 |
| typo zirconia | 244.448 ms | 245.102 ms | 1/1 -> 1/1 | 1 / 1.0 |

The typo-only case is effectively flat in the final run (+0.654 ms) with recall preserved.

The real-archive generic-facet sentinel executes high-frequency age/timing searches but yields **0 final evidence candidates** when its topic anchor is absent.

## Semantic-layer decision

No external embedding API, vector DB, heavyweight embedding dependency or new semantic index was added. Lexical + bounded conversation graph produced substantial precision/latency gains with zero DB-size increase, so an additional semantic dependency was not justified by benchmark evidence.

## Index / migration impact

- SQLite schema: **unchanged**
- index format: **unchanged**
- migration: **none**
- external service: **none**
- new dependency: **none**
- DB-size delta: **0 bytes**

## Risks / unresolved

1. `Discussion Recall@K` remains a bounded source-window proxy rather than a manually labelled discussion relevance set. A small human-adjudicated gold set is the main next measurement improvement.
2. Representative consolidation makes old message-level anchor recall not directly comparable and can reduce that proxy despite much better top-K discussion precision.
3. High-volume pediatric retrieval hits `MAX_DISCUSSIONS=140`; top-12 precision is 100%, but labelled recall should validate the cap or any adaptive expansion.
4. Required-facet mapping consumes `SearchPlan.required_aspects` plus family/query vocabulary; if the parallel planner branch changes naming semantics, rerun stage13 + full archive benchmark at integration.
5. Correction/disagreement cues are conservative metadata only and do not decide which clinical statement is correct.

## Integration dependencies / conflict hotspots

- `src/drjavanbot/ai/retrieval.py` is the main likely merge-conflict hotspot with other brain branches.
- Current `SearchPlan` is consumed unchanged.
- `RetrievalReport` implementation moved to `retrieval_contracts.py` but is imported/re-exported through `ai.retrieval` for current callers.
- Legacy match-reason aliases are retained.
- `semantic_benchmark.py` JSON schema is extended with discussion metrics and p95; strict consumers must tolerate the added fields.
- No planner/orchestrator/evidence/validator/synthesis change is required by this branch.

## Safety / archive integrity

- no hard-coded answer facts;
- no production ranking based on hard-coded archive message IDs;
- no model memory as evidence;
- no raw HTML edits;
- no runtime DB/secrets/sessions/logs/backups committed;
- no unbounded fuzzy/context scan;
- no merge to `main`;
- **raw archive untouched**.
