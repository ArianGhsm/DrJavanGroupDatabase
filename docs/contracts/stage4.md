# Stage-4 Telegram integration contract

Stage 4 must build Telegram UX on top of Stage 3; it must not bypass `ArchiveAnswerService` or send raw archive HTML to AvalAI.

## Question path

1. Telegram layer authenticates/access-checks/rate-limits the user.
2. Plain user question is passed to `ArchiveAnswerService.answer(question)`.
3. Only validated `AnswerResult` fields are rendered.
4. `source_refs` / `cited_message_ids` back the Telegram sources view.
5. `insufficient_evidence=true` must be rendered as a limitation, not replaced by a generic dental answer.

## Owner API-key path

- owner authentication must use numeric `TELEGRAM_OWNER_ID` and private chat only.
- candidate key is never echoed or logged.
- after receiving the key, Stage 4 should best-effort delete the Telegram message containing it.
- call `AvalAIKeyManager.validate_and_store(candidate)`.
- invalid candidate leaves the previous working key intact.
- successful replacement is immediately visible to new `ArchiveAnswerService` requests; restart is unnecessary.
- removal must call `manager.remove()` only after explicit owner confirmation.
- a later provider 401/403 must mark provider auth unhealthy but must not silently delete the stored key.

## Local runtime files

Recommended isolated paths:

- archive DB: `runtime/data/archive.sqlite3`
- AI usage DB: `runtime/data/ai_usage.sqlite3`
- response cache: `runtime/cache/ai_responses.sqlite3`
- AvalAI secret: `runtime/secrets/avalai_api_key`

All are already ignored by Git through the runtime directory policy.

## Owner statistics

Stage 4 can read:

- `ResponseCache.stats()` -> entries / hits / misses / expired entries
- `TelemetryStore.summary()` -> calls / successes / failures / input / cached-input / output tokens / total IRT cost / average latency
- Stage-2 `backend.stats()` -> archive/index statistics

Do not display or serialize the secret itself.
