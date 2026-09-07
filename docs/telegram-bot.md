# Telegram bot and owner operations — Stage 4

Stage 4 exposes the archive/AI pipeline through Telegram without putting any production secret in Git.

## Runtime secrets

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_ID` are supplied only by the server environment. `TELEGRAM_OWNER_ID` is a positive numeric Telegram user id; username/display name is never authorization.

The AvalAI API key is **not** an environment variable in the intended production flow. The bot starts without it. The owner opens the private chat, uses `/settings` → `Set/Replace API Key`, then sends the candidate key. The bot best-effort deletes that Telegram message before validation, validates through AvalAI `/v1/models`, and only then atomically stores the key at `runtime/secrets/avalai_api_key` with directory/file permissions targeted at `0700/0600`. An invalid replacement never deletes the previous working key.

## Commands

- `/start`, `/help`: normal UX.
- `/settings`: owner/private control panel.
- `/health`, `/stats`, `/reindex`: owner/private operations.
- `/allow NUMERIC_ID`, `/deny NUMERIC_ID`: owner/private allowlist maintenance.

Owner panel includes AvalAI configured/auth status, key set/replace/remove/test, model selection, usage/cost statistics, index statistics, health, cache statistics/clear, reindex, access mode and rate-limit controls.

## Access modes

Default is `owner_only`. Owner may choose:

- `owner_only`
- `allowlist` (numeric Telegram IDs only)
- `public`

Public/allowlist users are subject to a configurable per-user one-minute rate limit. Owner is exempt from this user rate limit. Questions are length-bounded before retrieval/API work.

## Model allowlist

Runtime selection is restricted to:

- `deepseek-v4-flash` (default)
- `deepseek-v4-pro`

The selection is persisted locally in `runtime/data/bot_state.sqlite3`. A new question constructs the Stage-3 service with the selected model, so switching does not require a process restart.

## Sources

Validated `cited_message_ids` / `source_refs` are converted into short-lived, user-bound source sessions. The inline `منابع` button pages through author/date/message-id/source-file metadata. A user cannot open another user's source session. The bot does not send raw archive files.

## Reindex

Reindex is owner-only and guarded by a process lock. Stage 2 already builds a temporary database and atomically swaps it only after validation, so the last known good index remains available on failure. Successful reindex clears response cache and records the last successful reindex timestamp.

## Long polling and isolation

The runtime uses Telegram Bot API long polling through Python standard-library HTTP. No Telegram framework dependency is required. The polling runner uses a bounded thread pool, persistent update-id deduplication and graceful SIGINT/SIGTERM shutdown. Stage 5/6 must run this bot under its own Unix user/app directory/service/env/runtime directories so it shares no process, virtual environment, token, database or secrets path with other bots.

## Failure behavior

- missing AvalAI key: bot remains up, questions return “AI not configured”.
- AvalAI 401/403: stored key is retained and provider-auth health becomes failed.
- 429: user-friendly rate-limit response, no unbounded retry.
- timeout/5xx/malformed provider response: generic safe message; no prompt, response, header or secret dump.
- missing index: bot remains up; owner health shows index failure and user receives index-not-ready.

## Runtime files

- `runtime/data/archive.sqlite3`
- `runtime/data/ai_usage.sqlite3`
- `runtime/data/bot_state.sqlite3`
- `runtime/cache/ai_responses.sqlite3`
- `runtime/secrets/avalai_api_key`

All remain outside Git.
