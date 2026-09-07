# Stage-5 hardening contract

Stage 5 must audit and harden the already-implemented parser/index/search/AI/Telegram stack. Do not rebuild working stages.

## Stable entrypoints

- local index CLI: `python -m drjavanbot ...`
- bot runtime: `python -m drjavanbot.telegram` or `drjavanbot-bot`
- owner config: numeric `TELEGRAM_OWNER_ID`; AvalAI key via private Telegram settings flow.

## Stage-5 priorities

1. End-to-end static/runtime audit across Telegram → local retrieval → AI → citations → render.
2. Run the full test matrix on a complete checkout with the real 247-page archive available locally.
3. Validate production SQLite/FTS5, reindex, cache invalidation, concurrency and graceful shutdown.
4. Validate Telegram long-poll smoke path with mocks/offline fixtures; no real token is required for code hardening.
5. Add deployment artifacts/docs for a unique Unix user/app directory/systemd service, runtime dirs, permissions and rollback.
6. Confirm no secret appears in Git, logs, exceptions, stats or docs.
7. Confirm all retries/rate limits/token budgets are bounded.
8. Produce `DEPLOYMENT.md` and `CODEX_DEPLOY_HANDOFF.md` so Codex performs only install/index/run/smoke/deploy and reports code bugs instead of refactoring broadly.

## Stage-4 runtime state

`BotStateStore` provides access mode, numeric allowlist, rate limit, update dedupe, owner flow TTL, question metadata, provider auth state, selected model, last successful reindex and user-bound source sessions.

Stage 5 may migrate/extend this state deliberately, but must preserve owner authorization semantics and no-secret storage.
