# DrJavanBot Quality Lab v2

`quality-eval` is an evaluation-only harness. It does not change planner, retrieval, evidence, synthesis, validation, runtime, archive files, or secrets.

## Contract

Run the same suite against any candidate checkout:

```bash
drjavanbot --archive-dir "گروه دکتر جوان" quality-eval \
  --top-k 12 \
  --base-sha "$(git rev-parse HEAD)" \
  --json-out quality-lab-report.json \
  --strict
```

The JSON schema is versioned as `quality-lab-v2.0`. Reports contain case IDs, categories, aggregate metrics, reason codes, counts, latency, token/call budgets, and truncated SHA-256 discussion identifiers. They do **not** contain archive message text, authors, phone numbers, emails, source locators, prompts, or model chain-of-thought.

## What is measured

- discussion Recall@K and first relevant discussion rank / MRR;
- topic relevance, facet co-location inside one hydrated bundle, context-only recovery;
- irrelevant-candidate rate, discussion concentration, author diversity;
- query, duplicate-family, hydration, candidate and discussion counts;
- false-insufficient / false-supported behavior in scripted end-to-end runs;
- invalid citations, quote mismatch and unsupported high-risk claim rejection;
- index time, DB size, median/p95 retrieval latency;
- logical AI-call and observable token budgets in a deterministic fake-provider run.

Real-archive gold metadata uses queries/facets plus safe discussion hashes. No dental answer is hard-coded from internet/model knowledge. The fake-provider suite uses synthetic evidence only and never requires an AvalAI secret.

## Baseline and thresholds

The BASE_SHA must be measured before numeric performance/relevance gates are frozen. A baseline report is therefore a separate integration artifact. Gates are not to be weakened merely to make a candidate green; a failing candidate should remain visible in its failure map.
