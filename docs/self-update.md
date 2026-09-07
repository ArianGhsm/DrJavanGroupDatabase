# Secure self-update architecture

After the one-time bootstrap, normal DrJavanBot code/archive updates no longer require Codex.

## Control flow

```text
Owner private Telegram /settings
  -> Software Update
  -> confirm
  -> bot writes /var/lib/drjavanbot/update/request.json
  -> drjavanbot-updater.path triggers root-owned oneshot service
  -> updater code is loaded from /opt/drjavanbot/current/deploy/self_update.py
  -> verify fixed HTTPS origin ArianGhsm/DrJavanGroupDatabase + clean control tree
  -> fetch origin/main (never live git pull)
  -> require fast-forward ancestry from active release
  -> create/verify /opt/drjavanbot/releases/<sha> worktree + release-specific .venv
  -> reconcile locked dependencies even after an interrupted prior install
  -> compile + full pytest with isolated TMPDIR and explicit --basetemp
  -> build complete staging archive index in isolated staging data
  -> health + local smoke + Telegram getMe
  -> only if all gates pass: stop only drjavanbot.service
  -> SQLite backup + atomic /opt/drjavanbot/current symlink switch
  -> production full reindex
  -> restart only drjavanbot.service and require stable-active window
  -> post-start smoke + Telegram check + stable-active check
  -> on any failure: previous symlink + SQLite state are restored and old bot restarted
```

The Telegram process never runs sudo, git, pip or arbitrary shell commands. It can only write a schema-validated `update` or `rollback` request. Repository URL, branch, service name and deployment paths are fixed in root-owned code.

## Why updater executes from `current`

The control repository `/opt/drjavanbot/app` exists only to fetch Git objects and manage worktrees. The root updater service executes `/opt/drjavanbot/current/deploy/self_update.py`, so a successful atomic release switch also changes the updater implementation used on the *next* update. This avoids a post-deployment `git reset --hard` of the control checkout and keeps a failed candidate from changing the updater used by the healthy active release.

Systemd unit-file changes themselves are privileged deployment changes and require rerunning the one-time bootstrap; ordinary Python/archive updates do not.

## Isolation

- control repository: `/opt/drjavanbot/app`
- tested releases: `/opt/drjavanbot/releases/<commit-sha>`
- active symlink: `/opt/drjavanbot/current`
- each release has its own `.venv`
- persistent DB/state/secrets: `/var/lib/drjavanbot`
- cache: `/var/cache/drjavanbot`
- request/result/history: `/var/lib/drjavanbot/update`
- bot service: `drjavanbot.service`
- updater: `drjavanbot-updater.service` + `drjavanbot-updater.path`

Nothing references or restarts other Telegram bots on the host.

## Two test gates

1. GitHub Actions runs compile, FTS5 verification, env parsing checks, full pytest and systemd unit verification on every push to `main`.
2. The server updater independently reruns compile + full pytest, with a private temp tree, then indexes the complete archive in an isolated staging data directory before touching production.

The server remains authoritative: a GitHub-green commit is not deployed unless server-side gates also pass.

## Archive updates

New or replaced `messages*.html` files committed to the repository are included in the candidate release. The updater builds a fresh staging index from that candidate archive, so malformed/new Telegram exports fail before the active bot is stopped. After a successful code switch the production index is rebuilt atomically.

## Runtime env invariant

Values containing whitespace, especially the Persian archive path, are stored quoted:

```text
DRJAVAN_ARCHIVE_DIR="/opt/drjavanbot/current/گروه دکتر جوان"
```

Bootstrap rewrites that key atomically, collapses duplicate copies of the key, preserves env-file mode/ownership, and CI verifies the env template can also be shell-sourced for manual smoke tests.

## Rollback

The updater keeps the last healthy releases (default 3) and a release history. Owner rollback switches to a previous release only if its worktree SHA, deployment marker and virtualenv all match. It rebuilds the archive index, restarts only DrJavanBot, and restores the pre-operation SQLite state if rollback validation itself fails. If no database existed before an attempted update, a failed deployment removes the newly-created database instead of leaving it behind.

## One-time bootstrap

```bash
cd /opt/drjavanbot/app
git pull --ff-only origin main
pytest -q
sudo python3 deploy/bootstrap_self_update.py
systemctl status drjavanbot.service --no-pager
systemctl status drjavanbot-updater.path --no-pager
```

Bootstrap is designed to be rerunnable: it validates the control repo and FTS5, repairs an interrupted release virtualenv, runs tests with an isolated temporary directory, preserves prior history, snapshots systemd units, atomically switches `current`, installs the fixed units, and restores the previous release/unit snapshot if activation fails.

After bootstrap, routine workflow is: ChatGPT changes GitHub -> CI -> Owner presses Software Update -> server gates -> atomic deployment.
