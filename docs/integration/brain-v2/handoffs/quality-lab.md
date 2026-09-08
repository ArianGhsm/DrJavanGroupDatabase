# PARALLEL HANDOFF — Brain Quality Lab v2

## Branch contract

- Repository: `ArianGhsm/DrJavanGroupDatabase`
- Branch: `parallel/brain-quality-lab-v2`
- Immutable parallel base: `4226f2c30a04d89a28afe8284be5c3e5f657a2a6`
- Branch was created directly from that SHA.
- No merge/rebase from newer `main` or any parallel branch was performed.
- No write to `main` was performed.
- Raw archive `گروه دکتر جوان/messages*.html` was not modified.
- Final evaluated HEAD is recorded by the final CI run and should be taken from the branch tip / integration report; documentation-only commits may follow a previously evaluated code HEAD.

## Evaluation architecture

Quality Lab v2 is isolated under `src/drjavanbot/ai/eval/**` with a compatibility export at `src/drjavanbot/ai/semantic_eval.py`. It does not alter the production planner, retrieval, evidence, orchestrator, or validation algorithms.

The deterministic full-archive layer builds one temporary SQLite index and reuses it for all retrieval cases. Each case is evaluated at discussion-bundle level rather than corpus-concatenation level. Topic and required facets must occur in the same hydrated bundle for a faceted case to count as recovered.

The second layer is secret-free scripted end-to-end evaluation with controlled provider outputs. Grounding red-team cases exercise the production answer validator deterministically without AvalAI or internet access.

## Stable report schema

Schema version: `quality-lab-v2.0`.

Top-level report sections:

- corpus/index metadata: `base_sha`, `top_k`, `index_seconds`, `db_size_bytes`, `archive_files`, `message_count`
- per-case metrics in `cases`
- aggregate retrieval/answerability/performance metrics in `aggregate`
- adversarial grounding results in `grounding`
- fake-provider E2E results and logical-call/token budgets in `scripted`
- PII-free gate reason codes in `strict_failures`
- explicit `pii_safe`

Reports intentionally exclude raw message text, author names, source locators, prompts, model responses, secrets, sessions and chain-of-thought. Discussion identities are short SHA-256-derived hashes over canonical archive position only.

## Golden suite

Case count: **37**.

Covered categories include:

- direct topic/product lookup
- recommendation/experience
- comparison
- age/timing/population
- cause/mechanism
- method/technique
- quantity/dose-like
- symptom/procedure relation
- short acronym
- mixed Persian/English
- typo/punctuation
- colloquial Persian
- fragmented Telegram discussion
- reply-parent answer
- correction/disagreement
- no-evidence sentinel
- low-information/noise
- privacy-sensitive probes
- generic high-frequency facet pollution
- duplicate query families
- author-diversity / echo diagnostics
- question beyond archive coverage

Four stable known-answerable cases are frozen to BASE_SHA discussion hashes: direct e.max, composite recommendation, pediatric orthodontic timing, and short RCT.

## BASE_SHA metrics

Two baseline measurements were intentionally performed during development: the first before final absent-case semantics/gates, and a verification measurement after those evaluator-only changes. Both use the same production brain from BASE_SHA; runtime variance is expected.

Representative measured values:

- archive files: **247**
- messages: **249,907**
- DB size: **373,555,200 bytes**
- first measured index build: **277.181 s**
- verification index build: **281.076 s**
- Discussion Recall@12 mean: **1.0000**
- MRR: **0.7812**
- topic relevance@12 mean: **0.8958**
- facet co-location rate: **1.0000**
- irrelevant candidate rate mean: **0.1042**
- first measured median retrieval: **2869.332 ms**
- first measured p95 retrieval: **3073.628 ms**
- median query count: **4**
- median hydration count: **12**
- scripted false-insufficient rate: **0.0000**
- scripted false-supported rate: **0.0000**
- grounding verifier accuracy: **1.0000**
- grounding adversarial cases: **12/12 passed**
- scripted E2E cases: **5/5 passed**
- scripted fragmented recovery: **1.0000**
- maximum scripted logical AI calls: **4**

The committed safe baseline snapshot is `docs/quality-lab/BASELINE_4226f2c30a04d89a28afe8284be5c3e5f657a2a6.json`.

## Frozen quality gates

Thresholds were set only after measuring BASE_SHA. They were not lowered in response to a strict-gate failure.

- Discussion Recall mean >= **1.00**
- MRR >= **0.70**
- topic relevance mean >= **0.85**
- facet co-location >= **0.95**
- irrelevant candidate rate mean <= **0.15**
- p95 retrieval latency <= **4000 ms**
- full index build <= **360 s**
- false-insufficient <= **0**
- false-supported <= **0**
- grounding verifier accuracy >= **1.00**
- invalid citation / quote mismatch / unsupported high-risk claim rates must remain **0**
- scripted reason-code accuracy and fragmented recovery must remain **1.00**
- max logical AI calls <= **4**
- median query count <= **6**
- median hydration count <= **16**

## Baseline failure / weakness map

### Retrieval/ranking

`ortho_pediatric_timing` is the clearest BASE_SHA weakness. The correct co-located discussion is recovered within K=12, but its first relevant discussion rank was **8** (RR **0.125**), topic relevance was **0.5833**, irrelevant rate **0.4167**, and the plan executed **11** queries. This is a retrieval/ranking/family-efficiency issue, not a discussion-co-location failure.

`comparison_emax_zirc` is observational rather than a strict known-answerable gold case. Its first relevant discussion appeared at rank **9** in the initial baseline. Integration should use this as a directional comparison diagnostic, not a hard correctness claim about the archive answer.

`generic_facet_pollution` measured topic relevance **0.75** and irrelevant rate **0.25**, illustrating why generic facet-only searches must not be treated as proof of an answer.

`duplicate_query_families` records duplicated family input independently from scheduler dedup so planner regressions cannot hide behind execution-time deduplication.

### Absent/no-evidence semantics

The first evaluator implementation treated any returned candidate as failure for an absent case. BASE_SHA showed that `beyond_archive` can produce fallback candidates while topic relevance is **0**, relevant discussion hashes are empty, and `supported_answer_observed=false`. The evaluator was corrected so absent safety means no relevant supported evidence, not literally zero lexical/fallback candidates. Production retrieval was not changed.

### Planner ownership

Cases with zero executable query runs receive `planner_no_queries` / `observe_planner_no_queries`. Duplicate-family diagnostics are also primarily planner-owned. The current four strict present cases do not fail this stage on BASE_SHA.

### Answerability/synthesis ownership

Secret-free scripted cases measure false-insufficient, false-supported, reason-code accuracy, and fragmented-discussion recovery. BASE_SHA scripted results are currently all passing. A future branch that retrieves the correct discussion but returns unsupported insufficiency should be classified here rather than as retrieval failure.

### Validation/provider ownership

Grounding red-team covers nonexistent message ID, wrong-message quote, topical citation laundering, invented numeric/high-risk facts, invented product/model, reversed comparison, dropped/reversed negation, model-memory style unsupported claims, malformed JSON and privacy-leak attempts. Failure reason codes belong to validation/provider handling, not retrieval.

## Files added / changed

Primary additions:

- `src/drjavanbot/ai/eval/__init__.py`
- `src/drjavanbot/ai/eval/schema.py`
- `src/drjavanbot/ai/eval/golden.py`
- `src/drjavanbot/ai/eval/runner.py`
- `src/drjavanbot/ai/eval/gates.py`
- `src/drjavanbot/ai/eval/grounding.py`
- `src/drjavanbot/ai/eval/scripted.py`
- `src/drjavanbot/ai/eval/reporting.py`
- `src/drjavanbot/ai/semantic_eval.py`
- `tests/test_quality_lab_schema.py`
- `tests/test_quality_lab_metrics.py`
- `tests/test_quality_lab_redteam.py`
- `tests/test_quality_lab_gates.py`
- `docs/quality-lab/README.md`
- `docs/quality-lab/BASELINE_4226f2c30a04d89a28afe8284be5c3e5f657a2a6.json`
- `PARALLEL_HANDOFF.md`

Modified integration surfaces:

- `src/drjavanbot/cli.py` — adds `quality-eval`; legacy `semantic-benchmark` remains available.
- `.github/workflows/tests.yml` — one full-archive strict Quality Lab run, PII-safe JSON artifact, read-only repository permissions.

No instrumentation hook was required in production planner/retrieval/orchestrator/evidence/validation modules.

## CI/runtime impact

The old semantic benchmark consumed about five minutes almost entirely on a full archive reindex. Quality Lab performs one full reindex and reuses it for all 37 retrieval cases plus deterministic scripted/grounding checks. The first Quality Lab full-archive phase completed in approximately **6m16s**. CI timeout remains 20 minutes.

Final workflow is non-secret and read-only (`contents: read`). It uploads only the safe machine-readable `quality-lab-report.json`; it does not auto-commit reports or mutate the branch.

## PII and artifact guarantees

- No raw archive chunks are copied into golden metadata.
- No author names or raw source locators are emitted in Quality Lab reports.
- No phone/email/address content is emitted by reports.
- No prompts, model outputs, API keys, headers, sessions, logs or chain-of-thought are included.
- Failure output is case ID + metric/reason code/owner only.
- Raw archive remains immutable and untouched.

## Integration instructions

For any candidate ref (BASE, planner branch, retrieval branch, evidence/reasoner branch, or merged candidate), check out that ref with the same immutable archive and run:

```bash
drjavanbot --archive-dir "گروه دکتر جوان" quality-eval \
  --strict \
  --top-k 12 \
  --base-sha 4226f2c30a04d89a28afe8284be5c3e5f657a2a6 \
  --json-out quality-lab-report.json
```

Compare reports only when `schema_version == "quality-lab-v2.0"` and the case IDs/gold metadata are unchanged. Do not tune the evaluator per candidate branch. A planner branch should improve query/family metrics without weakening recall; a retrieval branch should improve ranks/relevance/noise while preserving frozen discussion recovery; an evidence/reasoner branch should improve false-insufficient/synthesis behavior without increasing grounding failures.

For integration, review `failure_owner` first:

- `planner` -> query planning/schedulability
- `retrieval` -> candidate/discussion recovery, ranking, context or co-location
- `answerability` -> scripted synthesis/insufficient behavior
- `validation` / provider-related red-team -> grounded structured output handling

## Known limitations

- Discussion identity uses a bounded canonical-position bucket, not a manually annotated semantic thread graph. It is deliberately stricter than corpus-wide concatenation but remains an approximation for long interleaved Telegram conversations.
- Only four real-archive cases are currently frozen as strict known-answerable discussion gold. The remaining real-archive cases are observational diagnostics to avoid inventing dental truth from model/internet knowledge.
- Privacy-sensitive archive retrieval probes are observational; privacy safety is asserted primarily at report/artifact boundaries and by deterministic grounding red-team, not by declaring all phone/email lexical retrieval itself invalid.
- Performance thresholds are calibrated on GitHub `ubuntu-latest`; materially different runners should compare correctness separately from wall-clock gates.
- Live-AI evaluation is intentionally not required by CI. Token/call budgets in CI come from scripted providers and observable telemetry contracts.

## Raw archive status

**Confirmed: raw archive files are untouched.** No `گروه دکتر جوان/messages*.html` file was edited, generated, normalized, redacted, moved, or committed by this branch.
