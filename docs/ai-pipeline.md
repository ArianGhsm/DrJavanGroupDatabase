# AI pipeline and runtime interfaces

Stage 3 does not require a Telegram token or a real AvalAI key for tests.

## Runtime construction

```python
from pathlib import Path

from drjavanbot.ai import AIConfig, ArchiveAnswerService, ResponseCache, TelemetryStore
from drjavanbot.search import SQLiteSearchBackend
from drjavanbot.secrets import LocalFileSecretStore

backend = SQLiteSearchBackend(Path("runtime/data/archive.sqlite3"))
config = AIConfig.from_env()
secrets = LocalFileSecretStore(Path("runtime/secrets"))
cache = ResponseCache(Path("runtime/cache/ai_responses.sqlite3"), ttl_seconds=config.cache_ttl_seconds)
telemetry = TelemetryStore(Path("runtime/data/ai_usage.sqlite3"))

answers = ArchiveAnswerService(
    backend=backend,
    secret_store=secrets,
    config=config,
    cache=cache,
    telemetry=telemetry,
)

result = answers.answer("سؤال کاربر")
```

`ArchiveAnswerService.answer(question) -> AnswerResult` is the Stage-4 Telegram integration entry point.

## AnswerResult

Stage 4 may rely on:

- `direct_answer`
- `key_findings`
- `disagreements`
- `practical_conclusion`
- `confidence` (`high|medium|low`)
- `confidence_reason`
- `cited_message_ids`
- `source_refs`
- `evidence_used_count`
- `independent_authors_count`
- `insufficient_evidence`
- `safety_note_if_needed`
- `cache_hit`
- `ai_calls`
- `expansion_used`
- `evidence_pack_estimated_tokens`

Telegram must render `source_refs` as audit references and must never accept citations not returned by this validated object.

## SecretStore

Stable Stage-4 interface:

```python
store.get_secret(name) -> str | None
store.set_secret(name, value) -> None
store.delete_secret(name) -> bool
store.is_configured(name) -> bool
```

AvalAI secret name is exported as `AVALAI_API_KEY_SECRET`.

For owner setup, Stage 4 should use:

```python
manager = AvalAIKeyManager(store, AvalAIClient(config))
manager.validate_and_store(candidate_key) -> bool
manager.remove() -> bool
manager.configured() -> bool
```

The manager validates first and only then replaces the stored key. `ArchiveAnswerService` reads the current key at request time, so a successfully replaced key does not require a bot restart.

## Defaults / token limits

- simple: 2,500 evidence tokens, 14 packed messages, 450 output tokens
- medium: 5,000 evidence tokens, 26 packed messages, 750 output tokens
- complex: 8,000 evidence tokens, 40 packed messages, 1,100 output tokens
- global hard evidence cap: 9,000 estimated tokens / 40 messages
- query-expansion output: 180 tokens
- cache TTL: 86,400 seconds
- provider timeout: 25 seconds
- DeepSeek reasoning effort: `low` by default (cost/latency oriented)
- provider retry count: 2

Optional environment overrides:

- `DRJAVAN_AI_TIMEOUT_SECONDS`
- `DRJAVAN_AI_MAX_RETRIES`
- `DRJAVAN_AI_RETRY_BASE_SECONDS`
- `DRJAVAN_AI_REASONING_EFFORT` (`low|high|max`, default `low`)
- `DRJAVAN_AI_CACHE_TTL_SECONDS`
- `DRJAVAN_AI_EXPANSION_MAX_OUTPUT_TOKENS`
- `DRJAVAN_AI_SIMPLE_EVIDENCE_TOKENS`
- `DRJAVAN_AI_MEDIUM_EVIDENCE_TOKENS`
- `DRJAVAN_AI_COMPLEX_EVIDENCE_TOKENS`
- `DRJAVAN_AI_HARD_EVIDENCE_TOKENS`

Do not add `AVALAI_API_KEY` to Git-tracked config. Stage 4 obtains it from the owner in private Telegram chat and persists it through `SecretStore`.

## Error handling contract

Stage 4 should map these errors to user/admin-safe messages without showing secrets:

- `AIConfigurationError`: AvalAI key not configured
- `AuthenticationError`: configured key rejected (do not automatically delete it)
- `RateLimitError`: provider throttling
- `ProviderTimeoutError`: timeout after bounded retry
- `ProviderUnavailableError`: network/5xx exhaustion
- `ProviderResponseError`: invalid provider response
- `ModelOutputError` / `CitationValidationError`: fail closed; model output was not safe to render

No error handler should include request Authorization headers, stored API-key values, prompts containing private archive evidence, or raw provider response bodies.
