# PARALLEL HANDOFF — Brain / Query Planner v2

## Branch and locked base

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Branch: `parallel/brain-query-planner-v2`
- Locked base SHA: `4226f2c30a04d89a28afe8284be5c3e5f657a2a6`
- Implementation HEAD tested before this handoff metadata commit: `b7f79d464fe40ecc777f7b781488d2eca548c6be`
- GitHub Actions tested the PR merge of that implementation HEAD into the *same locked base* at synthetic merge SHA `7ca5db98bd6f75258fd41210e14e412bd4cfe52f`.
- The exact final branch HEAD necessarily changes when this handoff file itself is committed; a commit cannot embed its own final SHA in a file that participates in its hash. The final branch HEAD is therefore reported externally in the integration/final-chat handoff.
- No merge or rebase from a newer `main` or another parallel branch was performed.
- No merge to `main` was performed. Draft PR #1 exists only to exercise repository CI.

## Problem solved

The previous planner was a high-recall multi-query generator with a small deterministic timing/population rescue layer and a boolean deep-retrieval check. It did not expose a versioned query-understanding contract for answer facets, constraints, evidence shape, family purpose, budgets, rescue or stopping policy. That made short/direct lookups and multi-facet questions depend too heavily on prompt output shape, and made planner-cache payload compatibility implicit.

This branch turns the pre-retrieval layer into a bounded **Query Understanding + Retrieval Policy** while retaining the legacy `SearchPlan` surface expected by the current retrieval/orchestrator code. It does not move ranking, evidence selection or answer synthesis into the planner.

## Design rationale

1. **Typed/versioned contract.** `query-understanding-v1` adds explicit facets, anchors, constraints, evidence pattern and retrieval policy while preserving legacy fields and methods.
2. **AI + deterministic safety net.** AI may decompose meaning and propose terminology/aliases; generic deterministic language markers restore omitted facets and remain available when planner JSON is malformed/truncated.
3. **No clinical-answer knowledge in the planner.** Deterministic rules classify question form only. They contain no dental age, dose, guideline, product list or clinical conclusion.
4. **Novel-number guard.** A model-generated query/hint containing a number absent from the user's question is dropped. This blocks invented ages/doses/quantities from entering the retrieval plan. User-supplied numbers remain searchable but are still not evidence.
5. **Purpose-labeled families + breadth-first scheduling.** Topic/entity/facet/population/intersection/terminology/stage/alias families are distinct and bounded. Scheduling gives each selected family a first chance before spending budget on variants, and suppresses normalized near-duplicates.
6. **Adaptive depth.** Short non-faceted entity lookups are direct; comparison/recommendation/cause/method/timing-age/quantity/dose/etc. are deep and retain separate required facets. Recommendation/comparison expects multi-source evidence; timing/cause/method/quantity/dose-style questions can expect fragmented discussion.
7. **Cache versioning.** Planner version now includes the query-model schema version. Cached payloads also carry schema version and stale/incompatible payloads are evicted fail-soft.

## Files changed / added

### Added
- `src/drjavanbot/ai/query_model.py`
  - typed/versioned `SearchPlan` contract additions
  - `SearchFamily`, `RetrievalPolicy`, facet/evidence/depth/purpose enums
  - hard bounds and family-balanced query scheduling
  - legacy `SearchPlan.from_dict()` adapter
- `src/drjavanbot/ai/search_policy.py`
  - generic deterministic facet recognition
  - adaptive direct/standard/deep policy
  - comparison-target extraction from user wording
  - novel-number rejection and hint sanitation
- `src/drjavanbot/ai/planning_prompts.py`
  - compact schema-driven planning prompt
  - explicit non-evidence/non-answer constraints
- `tests/test_query_planner_v2.py`
  - planner/query-policy regression matrix

### Changed
- `src/drjavanbot/ai/planner.py`
  - typed + legacy parsing
  - deterministic fallback/query understanding
  - bounded purpose-labeled family construction
  - deterministic anti-omission layer
  - schema/version enforcement
  - backward-compatible deep-retrieval predicate
- `src/drjavanbot/ai/planner_cache.py`
  - schema-version validation/eviction
- `src/drjavanbot/ai/prompts.py`
  - runtime planner prompt now imports the typed planner prompt from `planning_prompts.py`; refinement/synthesis responsibilities remain unchanged
- `src/drjavanbot/ai/__init__.py`
  - exports query-model version and retrieval-policy contract

### Explicitly untouched
- `src/drjavanbot/ai/retrieval.py`
- `src/drjavanbot/ai/evidence.py`
- `src/drjavanbot/ai/validation.py`
- `src/drjavanbot/ai/orchestrator.py`
- `src/drjavanbot/ai/semantic_eval.py`
- retrieval/index internals
- raw archive `گروه دکتر جوان/messages*.html`
- runtime DB/data, secrets, sessions, logs and backups

## Public/internal contract changes

`SearchPlan` remains import-compatible and retains the legacy fields used by the current pipeline (`searchable`, `intent`, `core_concepts`, `aliases`, `optional_concepts`, `entity_types`, `query_families`, `phrases`, `exclude_terms`, `low_information_terms`, `reply_context`, `required_aspects`) plus legacy conveniences such as `.queries`, `.summary()`, `.to_dict()`, `.from_dict()` and `.with_added_families()`.

New typed fields expose:
- `schema_version`
- `normalized_intent`
- `topic_anchors`
- `answer_facets`
- population / condition / temporal constraints
- comparison targets
- terminology / colloquial / typo hints
- intersection queries
- negative hints
- expected evidence pattern
- `RetrievalPolicy` with depth, query/family/per-family budgets, rescue allowance/max families, stopping rule and minimum family coverage
- `anchor_families`

`SearchFamily` keeps `name` and `queries` and adds defaulted `purpose`, `priority` and `anchor`, so existing construction remains valid.

## Heuristics: reason, test and metric

| Heuristic/policy | Reason | Regression coverage | Bounded/measured result |
|---|---|---|---|
| Generic facet inference | Prevent planner omission from making faceted questions false-simple | pediatric timing, recommendation, comparison, cause, method, quantity, dosage | deep plans max 20 scheduled queries |
| Direct lookup policy | Avoid spending deep-retrieval budget on short entity lookups | `RCT?`, `e.max` | direct policy max 4 queries; semantic benchmark observed 3 query runs for each direct case |
| Purpose-labeled family balancing | Prevent one synonym family consuming the budget before required facets | duplicate/family-starvation tests | initial families max 8; final max 10; per family max 4; scheduler total max 20 |
| Near-duplicate suppression | Avoid query-budget waste on reordered/duplicated variants | duplicate/near-duplicate test | duplicates do not occupy scheduled slots |
| Deterministic repeated-character typo hint | Handle a common colloquial typo pattern without a dental dictionary | typo/spelling test | bounded to the normal hint/family limits |
| Novel-number rejection | Prevent planner-generated age/dose/quantity facts from leaking into search/evidence | dosage safety + user-supplied-number tests | invented number absent from serialized plan |
| Schema-versioned cache | Prevent structurally valid but semantically stale plans after planner/schema changes | stale payload + index/planner key invalidation tests | stale schema entry is deleted and returns cache miss |
| Evidence-pattern policy | Signal likely discussion shape without asserting an answer | recommendation/comparison and pediatric/cause/method tests | enum: single-message / fragmented / reply / multi-source |

## Tests and benchmark

### Planner-targeted matrix

`tests/test_query_planner_v2.py` contains 18 planner-specific regression tests covering:
- `کدوم برند کامپوزیت خوبه؟`
- `در بچه‌ها ارتودنسی رو در چه سنی باید استفاده کرد؟`
- `RCT?`
- `e.max`
- mixed Persian/English
- typo/repeated-character variants
- `e.max یا زیرکونیا؟`
- cause/reason
- method/how
- quantity and dosage-style questions without answer-fact generation
- non-searchable noise
- malformed/truncated planner JSON
- unsupported schema version
- duplicate/near-duplicate families/queries
- family breadth-first/required-facet anti-starvation
- planner cache stale-schema invalidation
- cache-key invalidation after index/planner version change
- prompt non-evidence contract
- typed/legacy round-trip compatibility

These targeted tests are collected and executed as part of the repository's final full `pytest -q` run. No temporary CI workflow was added solely to invoke the same test file separately.

### Full GitHub Actions result on implementation HEAD

Workflow run: `34184093058`  
Job: `101928841380`  
Result: **success**

- compile: PASS
- SQLite FTS5 verification: PASS
- env shell compatibility: PASS
- full pytest: **186 passed in 9.35s**
- full-archive semantic retrieval benchmark (`--strict --top-k 12`): **PASS**, `strict_failures=[]`
- systemd unit/path verification: PASS

Full-archive semantic benchmark metrics:
- archive files: **247**
- messages: **249,907**
- index time: **277.4229 s**
- benchmark DB size: **373,555,200 bytes**
- median retrieval latency: **2745.257 ms**
- pediatric orthodontic timing regression: gate PASS; 12 query runs; 5 families with hits; both required anchor groups hit and colocated
- composite recommendation: gate PASS; 7 query runs; 3 families with hits; top-k relevance proxy 1.0
- direct `e.max`: gate PASS; 3 query runs; 1 family with hits; top-k relevance proxy 1.0
- direct `RCT`: gate PASS; 3 query runs; 1 family with hits; top-k relevance proxy 1.0
- comparison `e.max یا زیرکونیا؟`: observational gate PASS; 6 query runs; 3 families with hits; top-k relevance proxy 1.0
- low-information noise: expected-no-retrieval gate PASS; 0 query runs
- no-evidence sentinel: expected-no-candidates gate PASS; 1 query run, 0 results

An earlier CI run exposed three contract regressions (comparison separator token boundary, legacy `topic` round-trip facet, legacy `facet_population` name). They were fixed before the successful run above; the successful run is the acceptance result.

## Query-count / latency / token-budget deltas

- Previous legacy scheduler had a global maximum of 20 scheduled queries. The new **direct** policy caps scheduled queries at 4, an **80% reduction in maximum scheduled-query allowance** for direct lookups.
- Deep questions retain an absolute maximum of 20 scheduled queries, but allocation is family-balanced and facet-aware rather than allowing early variants to monopolize the budget.
- Query-model hard bounds: initial families ≤8, post-refinement families ≤10, family variants ≤4, total scheduled queries ≤20.
- Real full-archive benchmark observations are listed above. Because this branch does not change retrieval/index internals and the CI benchmark includes indexing/candidate/context work, the measured retrieval latency is recorded as an acceptance metric, **not claimed as a causal latency improvement versus base**.
- No external planner-provider call/token benchmark is available in CI, so no fabricated provider latency or token-savings number is claimed. The planner output/cost loop remains bounded by the existing orchestrator/provider limits; this branch adds structural family/query bounds and a compact prompt but does not claim an empirically measured model-token delta.

## Known risks / unresolved items

1. **Integration consumer adoption.** The current retrieval/orchestrator remains backward-compatible and therefore consumes the legacy-compatible surfaces. A future retrieval integration can additionally use `anchor_families`, `retrieval_policy.minimum_family_coverage`, rescue/stopping fields and evidence pattern directly; this branch intentionally does not rewrite retrieval internals.
2. **Response-cache visibility.** Planner-cache invalidation explicitly includes planner/schema/index changes. Integration should verify whether any higher-level final-answer cache key needs planner-version participation if immediate post-deploy planner behavior must bypass already-valid grounded-answer cache entries.
3. **Deterministic language markers are deliberately generic.** They improve fail-soft facet coverage but are not intended to replace AI semantic decomposition for arbitrary phrasing.
4. **Typo fallback is deliberately conservative.** The deterministic layer only applies mechanical normalization/repeated-character collapse; semantic spelling variants remain bounded AI retrieval hints rather than a hard-coded dental dictionary.
5. **Benchmark causality.** Full-archive semantic benchmark passed, but this branch does not claim ranking/index quality improvement because those internals are outside branch ownership.

## Integration dependencies / likely conflicts

- `src/drjavanbot/ai/planner.py`: **HIGH** conflict likelihood with any parallel planner/query-understanding branch.
- `src/drjavanbot/ai/prompts.py`: **MEDIUM** conflict likelihood with prompt/synthesis branches; this branch only wires the planner prompt import, so integration should preserve other branches' refinement/synthesis changes.
- `src/drjavanbot/ai/__init__.py`: **LOW** conflict likelihood; union exports if needed.
- `src/drjavanbot/ai/planner_cache.py`: **LOW–MEDIUM** if another branch changes cache serialization/versioning.
- New `query_model.py`, `search_policy.py`, `planning_prompts.py`: integrate as the planner contract/policy implementation.
- `retrieval.py`, `orchestrator.py`, `evidence.py`, `validation.py`, `semantic_eval.py`: untouched by this branch; if parallel branches change them, consume the typed contract through the backward-compatible adapter rather than moving answer facts into planner code.

## Safety / source-of-truth confirmation

- Raw archive `گروه دکتر جوان/messages*.html`: **untouched**.
- No dental factual answer, age rule, dose rule, guideline or approved brand list was encoded in deterministic planner logic.
- Search hints remain non-evidence; final factual claims still require retrieved archive messages and the existing grounded synthesis/validation path.
- Runtime DB/data, secrets, sessions, logs and backups: **untouched**.
- No new heavy dependency or external clinical service was added.
- No write was made to any repository other than `ArianGhsm/DrJavanGroupDatabase`.
- No merge to `main` was performed.
