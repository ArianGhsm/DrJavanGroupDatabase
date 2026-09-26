# Intelligence v2 Evaluation

## Deterministic Intelligence Lab

Final Stage 2 code-candidate run contains **125 questions** across categories A-J, including 13 Oral Pathology cases and 35 distinct facets. Results: required-facet recall **125/125**, exact facet set **125/125**, primary routing **125/125**, archive-specific intent **125/125**, failures **0**.

## Frozen archive Quality Lab

The lab builds an isolated temporary SQLite index from the production archive source files; production DB is read-only and unchanged. Final verified metrics are recorded from the committed code candidate:

- 247 files / 249,907 messages
- 37 cases / 0 strict failures
- Recall@12 = 1.0000; MRR = 1.0000
- topic relevance@12 = 1.0000; facet colocation = 1.0000
- irrelevant candidate rate = 0.0000
- false-insufficient = 0.0000; false-supported = 0.0000
- grounding red-team = 12/12; verifier accuracy = 1.0000
- scripted E2E = 5/5
- index build 292.181 s; median retrieval **1295.348 ms**; p95 retrieval **1746.146 ms**.

## Live reliability gate

Fresh-cache repeated gate on code SHA `c7ff5d00ba9cc0521b3f30894eaff3305fa8bf36`:

| Class | Runs | Expected AI calls | Result |
|---|---:|---:|---|
| archive e.max | 5 | 0 | 5/5 supported |
| hybrid e.max | 5 | 1 | 5/5, Archive + Scientific |
| cyst prevalence | 3 | 1 | 3/3 Scientific |
| Iran new-graduate salary | 3 | 1 | 3/3 Current, amount + currency + year |
| `RCT?` | 3 | 0 | 3/3 archive fast path |
| `zqv-99` sentinel | 3 | 0 | 3/3 insufficient before synthesis |

Aggregate: **22/22**, failed runs **0**, repairs **0**, structured failures **0**, model-call rows **11**, p50 **3.068 s**, p95 **6.608 s**, input tokens **17,241**, output tokens **3,040**, reported cost **829.20 IRT**. Hybrid runs used `deepseek-v4-flash`; hybrid alone did not force STRONG.

## Cache gate

All six smoke classes were repeated with a temporary source-aware cache. Second requests were cache hits with **0 synthesis calls**. Cache contract/grounding/retrieval versions are bumped to v2.1 to invalidate pre-stabilization entries.

## Interpretation

Scientific/current correctness is authority-gated rather than LLM-graded. Citation validity is structural: model output contains support IDs only; quote/source metadata is application-owned. No-evidence and adversarial cases fail closed. Dense retrieval remains disabled because no benchmark demonstrated material gain.
