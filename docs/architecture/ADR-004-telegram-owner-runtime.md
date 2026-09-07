# ADR-004 — Telegram runtime and owner control plane

Status: Accepted — Stage 4

## Decisions

1. Telegram transport uses direct Bot API long polling with Python standard-library HTTP. This keeps production dependencies minimal and makes deployment/smoke testing explicit.
2. Authorization is numeric `TELEGRAM_OWNER_ID`; all secret/admin callbacks are re-authorized and require private chat.
3. The AvalAI key is entered after startup through the owner's private chat, deleted best-effort from Telegram, validated before atomic storage, and never echoed/logged.
4. Bot runtime state is a separate SQLite database containing non-secret settings, allowlist, rate events, processed update ids, question-level metadata and short-lived source sessions.
5. Default access is `owner_only`; `allowlist` and `public` are explicit owner choices.
6. Model selection is an allowlist (`deepseek-v4-flash`, `deepseek-v4-pro`) and is applied per new request without restart.
7. Reindex remains Stage-2 full atomic reindex, wrapped in an owner-only non-blocking runtime lock. Response cache is cleared after successful reindex.
8. Telegram text uses HTML escaping and bounded chunking. No user text is interpolated into SQL/shell/path operations.
9. Source sessions are short-lived and bound to the requesting numeric user id. Raw archive export is never sent by the bot.
10. Question/update telemetry persists metadata only; question text, prompts, model responses and secrets are not copied into bot usage tables.

## Consequences

- Deployment needs only the repository, Python environment, Bot Token and Owner ID; AvalAI secret can be configured later in Telegram.
- A revoked AvalAI key does not disappear automatically; owner sees failed auth and can replace/remove it intentionally.
- Long polling avoids a public webhook/port requirement and simplifies isolation on a shared server.
- In-memory reindex lock is process-local; Stage 5 must ensure a single service instance or add an OS-level lock if multi-instance deployment becomes a requirement.
