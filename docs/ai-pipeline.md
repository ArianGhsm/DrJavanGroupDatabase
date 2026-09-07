# AI pipeline and runtime interfaces

DrJavanBot answers from the Telegram archive only. DeepSeek/AvalAI may plan searches, refine retrieval, select relevant evidence, and synthesize prose, but model memory is never factual evidence.

## Production retrieval pipeline

```text
question
  -> normalize + reject empty/punctuation-only input
  -> answer-cache lookup
  -> semantic Search Planner (small JSON call, or index-bound plan-cache hit)
  -> remove low-information query words
  -> independent local FTS5/BM25/exact/fuzzy query families
  -> deterministic score fusion + author/thread diversity + reply/context expansion
  -> coverage/diversity assessment
  -> if weak: one bounded AI refinement using vocabulary observed in the current archive/index
  -> final evidence pack
  -> evidence-selection + synthesis JSON call
  -> strict citation validation
  -> AnswerResult
```

The planner never answers the question. Planner terms, aliases, product-like vocabulary and corpus hints are **search hints only**. A hint becomes usable evidence only when a subsequent local search retrieves an actual archive message and that message enters the validated evidence pack.

Unknown product/brand names are not hard-coded. The planner prompt explicitly avoids inventing unmentioned product names; weak retrieval can instead discover product-like vocabulary from the current FTS index and reply/context messages.

No external vector database or embedding service is required by this architecture. The local SQLite/FTS5 index stays deterministic and auditable while AI supplies semantic intent/query planning.

## AI call budget

Logical AI calls are globally bounded at three per uncached request:

- normal first request: **2 calls** — Search Planner + synthesis;
- weak retrieval: **3 calls** — Search Planner + one refinement + synthesis;
- normal malformed synthesis: **3 calls** — planner + synthesis + one structured repair retry;
- weak retrieval + malformed synthesis: no fourth call; fail closed with the safe fallback;
- answer-cache hit: **0 calls**;
- plan-cache hit with answer-cache miss: normally **1 call** — synthesis only.

The provider itself may perform its existing bounded transport retries; those do not create an application-level search loop.

## Search Planner

The compact structured plan contains bounded fields such as:

- intent;
- core concepts;
- Persian/English aliases and likely spelling forms;
- optional concepts;
- desired entity types;
- up to five initial query families;
- phrases/exclusions/low-information terms;
- whether reply/thread context is important.

Planner parsing permits at most 14 lexical query executions in a final plan. Refinement can add up to three bounded families while the 14-query ceiling still applies.

Common question/filler words such as `چرا`, `چی`, `کدوم`, `آیا`, `خوبه`, `بگو`, `کسی` and `میشه` are deterministically prevented from dominating lexical retrieval. Clinical/product terms are not removed by this filter.

## Retrieval and context

Each query family is searched separately by the existing local search stack: FTS5/BM25, exact phrase matching, normalized Persian/English text, static dental aliases and bounded fuzzy spelling expansion. Results are fused deterministically using local relevance, reciprocal-rank contribution, cross-family coverage, exact/normalized matches, reply/context availability, and soft diversity penalties for repeated authors or very-near messages.

Reply chains and nearby context remain bounded. A short reply such as «من اینو خیلی دوست داشتم» can therefore be linked to the message/product it replies to without turning an entire surrounding conversation into evidence.

When first-pass evidence is weak, refinement receives only bounded vocabulary observed from the candidates/context plus co-occurring terms derived from the **current canonical SQLite index**. This corpus-aware step needs no schema migration or persistent per-question dynamic index.

## Grounding and evidence selection

The final AI call is both an evidence selector and a synthesizer. Retrieval inclusion is not treated as proof of relevance or truth. The model is instructed to reject off-topic candidates, consider reply chains, independent authors, corrections, disagreement and promotional/experiential context, then cite only evidence actually supplied by the application.

Citation validation remains fail-closed: every `message_id` and `source_ref` must occur in the evidence pack. `insufficient_evidence=true` is a valid final result.

## Runtime construction

```python
from pathlib import Path

from drjavanbot.ai import AIConfig, ArchiveAnswerService, ResponseCache, SearchPlanCache, TelemetryStore
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.secrets import LocalFileSecretStore

backend = SQLiteSearchBackend(Path("runtime/data/archive.sqlite3"))
config = AIConfig.from_env()
secrets = LocalFileSecretStore(Path("runtime/secrets"))
cache = ResponseCache(Path("runtime/cache/ai_responses.sqlite3"), ttl_seconds=config.cache_ttl_seconds)
planner_cache = SearchPlanCache(Path("runtime/cache/search_plans.sqlite3"), ttl_seconds=config.cache_ttl_seconds)
telemetry = TelemetryStore(Path("runtime/data/ai_usage.sqlite3"))

answers = ArchiveAnswerService(
    backend=backend,
    secret_store=secrets,
    config=config,
    cache=cache,
    planner_cache=planner_cache,
    telemetry=telemetry,
)
```

The Telegram runtime uses the resilient response cache implementation but the same service contract.

## Cache invalidation

Answer and search-plan cache keys include the current index fingerprint. The plan key also includes `PLANNER_VERSION` and model identity. Reindexing clears both runtime caches, and a changed database fingerprint prevents stale plans from being reused even without destructive cache migration.

## Current token ceilings

- search planner output: 320 tokens;
- weak-path refinement output: 220 tokens;
- simple evidence: 2,500 estimated tokens / 14 messages;
- medium evidence: 5,000 / 26 messages;
- complex evidence: 8,000 / 40 messages;
- hard evidence cap: 9,000 / 40 messages;
- synthesis output: 800 / 1,200 / 1,800 tokens for simple / medium / complex;
- structured synthesis repair ceiling: 2,400 tokens;
- cache TTL: 86,400 seconds;
- provider timeout: 25 seconds.

DeepSeek V4 production requests explicitly use `thinking: {"type":"disabled"}`. Generation ceilings are maximums, not prepaid usage.

Optional environment overrides include:

- `DRJAVAN_AI_TIMEOUT_SECONDS`
- `DRJAVAN_AI_MAX_RETRIES`
- `DRJAVAN_AI_RETRY_BASE_SECONDS`
- `DRJAVAN_AI_CACHE_TTL_SECONDS`
- `DRJAVAN_AI_PLANNER_MAX_OUTPUT_TOKENS`
- `DRJAVAN_AI_REFINEMENT_MAX_OUTPUT_TOKENS`
- `DRJAVAN_AI_SIMPLE_EVIDENCE_TOKENS`
- `DRJAVAN_AI_MEDIUM_EVIDENCE_TOKENS`
- `DRJAVAN_AI_COMPLEX_EVIDENCE_TOKENS`
- `DRJAVAN_AI_HARD_EVIDENCE_TOKENS`
- `DRJAVAN_AI_SIMPLE_OUTPUT_TOKENS`
- `DRJAVAN_AI_MEDIUM_OUTPUT_TOKENS`
- `DRJAVAN_AI_COMPLEX_OUTPUT_TOKENS`
- `DRJAVAN_AI_STRUCTURED_RETRY_OUTPUT_TOKENS`

Do not place `AVALAI_API_KEY` in Git-tracked config. It remains in the existing secret store.

## Regression/QA contract

CI covers planner JSON parsing, malformed planner fallback, provider timeout/rate limit, low-information filtering, multi-query fusion, reply/context handling, author diversity, fake citation rejection, cache invalidation, the three-call ceiling, and the existing deployment/updater suite. A real-archive regression copies a bounded subset of actual Telegram HTML pages to a temporary directory, builds the canonical SQLite index, and verifies semantic composite retrieval plus current-index corpus hints without hard-coding a recommended brand or modifying the source archive.
