# Telegram bot and owner operations

DrJavanBot exposes the archive-grounded retrieval/synthesis pipeline through Telegram without putting production secrets in Git.

## Answer UX

User-visible answers are explicitly presented as **«جمع‌بندی پیام‌های گروه»**. The Telegram archive is the only factual source. AI may plan retrieval, refine searches and select relevant archive evidence, but it is not an independent answer source.

A supported answer carries claim-level verified archive supports. The UI surfaces a bounded set of the exact verified support quotes with their message IDs, and the `منابع` button opens a user-bound source session for the underlying archive messages. If sufficient archive evidence is not found, the UI says so and does not fill the answer from general model knowledge.

Where supported by the active Telegram Bot API, presentation uses Rich Messages with RTL content, headings, blockquotes, lists, separators and footers. Rich rendering is presentation-only: if it is rejected, the same already-computed answer falls back to legacy HTML without repeating retrieval, AI calls or owner actions. `DRJAVAN_TG_RICH_UI_ENABLED=false` disables Rich UI without changing answer semantics.

Long answers retain the safe legacy chunk limit rather than sending oversized Rich payloads.

## Runtime secrets

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_ID` are supplied only by the server environment. `TELEGRAM_OWNER_ID` is a positive numeric Telegram user id; username/display name is never authorization.

The AvalAI API key is **not** an environment variable in the intended production flow. The bot starts without it. The owner opens the private chat, uses the owner panel to set/replace the API key, then sends the candidate key. The bot best-effort deletes that Telegram message before validation, validates the candidate, and only then atomically stores it with restrictive permissions. An invalid replacement never deletes the previous working key.

## Owner commands and panel

Public commands include `/start` and `/help`. Owner/private operations include:

- `/panel` or `/settings` — owner control panel;
- `/health` — runtime health;
- `/stats` — usage/index statistics;
- `/reindex` — atomic archive reindex;
- `/update` — staged software update status/action;
- `/errors` — recent safe error IDs/classes;
- `/allow NUMERIC_ID`, `/deny NUMERIC_ID` — allowlist maintenance.

The owner panel exposes AvalAI status/key controls, model selection, usage/cost statistics, index/health/cache controls, reindex, access/rate controls, recent errors, and the staged software updater.

## Software update UX

The update page separates current state, current/target release SHA, action/request ID, updater message and last status timestamp. Update and rollback remain explicit owner/private actions. A new GitHub version may notify the owner, but **notification never implies auto-install**.

The updater builds/tests/stages the candidate while the active bot remains available and switches only after the configured gates pass. Rollback is a separate confirmed action.

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

The model can assist retrieval and evidence selection, but the answer validator enforces the archive-only factual contract regardless of model selection.

## Sources

Validated `cited_message_ids` / `source_refs` are derived locally from verified claim supports and converted into short-lived, user-bound source sessions. A user cannot open another user's source session. The bot does not send raw archive files.

## Reindex

Reindex is owner-only and guarded by a process lock. Index construction uses a temporary database and atomically swaps only after validation, so the last known good index remains available on failure. Successful reindex clears response/search-plan caches and records the last successful reindex timestamp.

## Long polling and isolation

The runtime uses Telegram Bot API long polling through Python standard-library HTTP with a bounded worker pool, persistent update-id deduplication and graceful shutdown. DrJavanBot runs under its own app/service/env/runtime paths and must not share tokens, databases, virtual environments or secret paths with other bots.

## Failure behavior

- missing AvalAI key: bot remains up and reports configuration status;
- AvalAI 401/403: stored key is retained and provider-auth health becomes failed;
- 429: user-friendly rate-limit response with no unbounded retry;
- timeout/5xx/malformed structured output: bounded retry/fail-closed behavior; no prompt, response, header or secret dump;
- invalid or unsupported synthesis claims: rejected by archive grounding validation;
- insufficient archive evidence: deterministic group-not-found answer;
- missing/busy index: bot remains up and reports index-not-ready;
- Rich Message presentation failure: fallback to already-computed legacy HTML only.

## Runtime files

Production generated data, caches and secrets remain outside Git. The active deployment uses the configured `/var/lib/drjavanbot`, `/var/cache/drjavanbot` and secret/update paths rather than committing runtime state to the repository.
