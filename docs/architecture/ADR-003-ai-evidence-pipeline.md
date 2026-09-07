# ADR-003 — AvalAI/DeepSeek evidence pipeline

Status: Accepted — Stage 3

## Context

Stage 2 already provides bounded local retrieval over the complete Telegram archive. Stage 3 must add AI assistance without turning DeepSeek into a general dental chatbot or sending the archive to the model.

## Decisions

### Provider

- AvalAI is accessed through its OpenAI-compatible base URL, default `https://api.avalai.ir/v1`.
- Default model is `deepseek-v4-flash`.
- Chat endpoint is `/chat/completions`.
- Provider configuration is centralized in `AIConfig`; secrets are never part of `AIConfig` or Git-tracked configuration.
- `reasoning_effort=low` is the default for the V4 Flash path to prioritize cost/latency; it remains configuration-driven.
- The client uses bounded retry/backoff for network timeout, 429 and 5xx. 401/403 are never retried.
- `/models` is used for low-cost API-key validation before a new key is stored.

### One-call normal path

Normal flow:

`question -> Stage-2 local search -> compact evidence pack -> one synthesis call`

A separate query-expansion call is allowed only when deterministic retrieval assessment reports no/weak evidence. Expansion returns only search variants; the variants are sent back through `SearchQuery.variants` and local retrieval runs again.

If expansion still produces no candidates, the application returns an insufficient-evidence answer without spending a synthesis call.

### Evidence-only synthesis

The stable system prompt explicitly prohibits using model memory as a factual source. The user payload contains only the question plus a bounded structured evidence pack. The model must return JSON and cite only supplied `message_id` / `source_ref` values.

The application rejects invented citations. `evidence_used_count` and `independent_authors_count` are recomputed locally rather than trusted from model output. High confidence is locally capped when the cited evidence is not independent or the answer reports disagreements.

### Token control

Default evidence budgets are:

| profile | evidence estimate | max packed messages | max synthesis output |
|---|---:|---:|---:|
| simple | 2,500 tokens | 14 | 450 tokens |
| medium | 5,000 tokens | 26 | 750 tokens |
| complex | 8,000 tokens | 40 | 1,100 tokens |

Hard evidence cap: 9,000 estimated tokens and 40 packed messages. Query expansion output cap: 180 tokens.

Token estimation is deliberately conservative (`~3 Unicode characters/token`) and is used only as a local guardrail. Provider-reported usage remains authoritative for telemetry/billing statistics.

### Cache

Cache key includes normalized question, an index fingerprint, prompt version, model, and token-budget signature. The real SQLite backend fingerprint includes current backend stats plus DB file identity/size/mtime; therefore full or incremental reindex changes the key without requiring a schema change in Stage 2.

Cache persistence is local SQLite. Hits/misses are counted. Cache contains answer data, never API keys.

### Telemetry

A separate local SQLite telemetry DB records metadata only:

- request type (`expansion` / `synthesis`)
- model
- input tokens
- cached input tokens if reported
- output tokens
- provider cost metadata if reported
- latency
- success/failure and exception class

Prompts, answer bodies, Authorization headers and API keys are not persisted.

### Secret storage

`LocalFileSecretStore` stores each secret as a separate local file, normally under `runtime/secrets/`, with best-effort directory mode `0700` and file mode `0600`. Writes are temporary-file + fsync + atomic replace.

No pretend encryption is used. Production security is based on Unix-user/process isolation and filesystem permissions. A candidate AvalAI key is validated before atomic replacement, so an invalid replacement does not destroy a working key.

## Limitations

- Citation validation proves that cited sources were supplied to the model; it cannot prove semantic entailment of every sentence without another model call. To preserve the low-token design, Stage 3 fails closed on malformed output and uses strong evidence-only prompting rather than an additional verification call.
- Obvious phone numbers, email addresses and Telegram invite links are masked before evidence is sent to the model. Other sensitive data is controlled primarily through the synthesis privacy policy and Telegram rendering layer.
- Actual token counts and costs depend on AvalAI provider responses; local estimates are only packing limits.
