# DrJavanBot Admin Control Center v2

## Scope

Admin Control Center v2 redesigns only the owner/admin and release-management surfaces. Archive evidence rules, retrieval, grounding, clinical-answer composition, and the Telegram question path are unchanged.

## Product hierarchy

The owner entry points (`/start`, `/panel`) open one compact dashboard with six destinations:

1. **به‌روزرسانی** — installed/latest SHA, CI state, update mode, history and rollback.
2. **سلامت سیستم** — bot, AI, archive/index, updater and storage state.
3. **هوش مصنوعی** — API-key lifecycle, connectivity test and model selection.
4. **آرشیو و ایندکس** — message/index status and explicit reindex confirmation.
5. **دسترسی کاربران** — access policy and rate limit.
6. **ابزارها** — statistics, cache and recent safe error identifiers.

All reusable owner-facing terminology is centralized in `telegram/admin_ui.py`. English identifiers remain internal unless a technical value such as a model name or commit SHA is useful to the owner.

## Update modes

The mode is persisted in the existing `bot_settings` table; no database migration is required.

- `notify` — default. Check for a newer `main`, report it once, and require owner installation.
- `auto` — automatically request an update only when the exact current `main` SHA has a successful `DrJavanBot tests` GitHub Actions run.
- `off` — disable periodic remote update discovery. Manual Update Center refresh remains possible.

Changing to automatic mode requires an explicit confirmation screen.

## Exact-SHA CI gate

Both the unprivileged bot-side control plane and the privileged updater independently verify the exact target SHA. The privileged updater additionally requires the requested SHA to equal the freshly fetched `origin/main` and to be a fast-forward descendant of the active release.

CI states are fail-closed:

- `success`: eligible for installation.
- `pending`: do not install.
- `failure`: do not install.
- `unknown`: do not install.

The target workflow is fixed to `DrJavanBot tests` in the fixed repository `ArianGhsm/DrJavanGroupDatabase`.

## Update state machine and live progress

The root updater writes structured schema-v2 state into `result.json`. UI does not parse journal text.

The user-facing flow is seven conceptual stages:

1. بررسی نسخه
2. بررسی تست‌های GitHub
3. آماده‌سازی نسخه
4. اعتبارسنجی production
5. فعال‌سازی
6. بررسی سلامت و تلگرام
7. پایان

Detailed states include `pending`, `checking`, `preparing`, `validating`, `staging`, `switching`, `verifying`, `success`, `failed`, `rolling_back`, and `rolled_back`.

When an installation/rollback is started from Telegram, the request is bound to that Telegram `chat_id/message_id`. The update monitor reads only structured local state every few seconds and edits that same message. Remote GitHub checks remain low-frequency. This avoids chat spam and makes long operations observable.

Terminal failures expose a safe `UPD-xxxxxx` identifier. Secrets, raw user questions and archive text are not put in updater state or Telegram diagnostics.

## Incremental release classifier

The updater classifies the diff between the active release and exact target SHA:

- `code_only` — application/presentation code only.
- `dependency` — dependency lock/build metadata changed.
- `index` — parser/search/storage/index semantics changed.
- `archive` — the tracked Telegram archive changed.
- `mixed` — more than one heavy class or initial release.

### Code-only

- immutable worktree candidate
- reuse/copy the current healthy venv when safe
- install the local project without dependency resolution
- compile
- production health + local smoke + Telegram smoke
- atomic switch
- final health/smoke
- **no production pytest, Quality Lab or reindex**

### Dependency

- create/reconcile a fresh venv from locked dependencies
- compile
- production health + smoke
- atomic switch
- **no reindex unless another index/archive class is also present**

### Index/archive/mixed

- prepare candidate release before downtime
- build a staged SQLite index using private temporary paths
- health + smoke against the candidate
- atomic switch and staged DB promotion
- final health + Telegram smoke

GitHub CI remains authoritative for pytest and the Full Archive Quality Lab. Production repeats only gates needed to prove that the already-tested artifact works with the actual runtime environment.

## Release and rollback safety

Preserved invariants:

- fixed repository and HTTPS remotes only
- immutable release worktrees
- fixed `/opt/drjavanbot` layout
- atomic `current` symlink switch
- previous healthy releases retained
- SQLite backup/restore around DB promotion
- fixed `drjavanbot.service` only
- no arbitrary shell action from Telegram
- no `git reset --hard`
- no broad cross-project permission changes
- secret-redacted subprocess errors

Rollback also prepares a compatible staged database before the short final switch rather than doing a long production reindex while the bot is down.

## One-release migration compatibility

Production before v2 uses `deploy/self_update.py` as the fixed systemd entrypoint. To avoid a bootstrap dependency during this migration:

- bot-side update requests intentionally remain request `schema=1` with optional v2 metadata;
- the old updater safely ignores the optional fields and can deploy this release once;
- this release keeps the same `self_update.py` path, but it becomes a compatibility dispatcher to `update_engine_v2.py`;
- after the release switch, future updater invocations automatically use v2.

This allows the update subsystem to replace itself without changing the privileged service target during the critical migration.

## History and notifications

`history.json` is retained for legacy healthy-release rollback ordering. `history-events.json` adds a bounded operational history with target/current SHA, action, result, timestamps, duration, change class and safe error ID.

Notification claims are keyed by SHA plus event kind, preventing repeated “new version” messages while still allowing a later success/failure event for the same SHA.

## Performance expectation

The old generic update path repeated full pytest and a complete staging reindex on every update. V2 removes those steps for code-only/dependency-only releases and relies on exact-SHA CI for the full test/Quality Lab gate. The main remaining code-only costs are Git fetch/worktree preparation, local package install/compile, health/smoke, and the final short service switch.

Exact wall-clock improvement is environment-dependent and must be reported only after a real production update has been measured.
