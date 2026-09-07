# Secure self-update architecture

After the one-time bootstrap, normal DrJavanBot code/archive updates no longer require Codex.

## Control flow

```text
Owner private Telegram /settings
  -> Software Update
  -> confirm
  -> bot writes /var/lib/drjavanbot/update/request.json
  -> drjavanbot-updater.path triggers root-owned oneshot service
  -> verify fixed origin ArianGhsm/DrJavanGroupDatabase + origin/main
  -> fetch only (no live git pull)
  -> create isolated /opt/drjavanbot/releases/<sha> worktree + .venv
  -> compile + full pytest
  -> build a complete staging archive index in isolated staging data
  -> health + smoke + Telegram getMe
  -> only if all gates pass: stop only drjavanbot.service
  -> SQLite backup + atomic /opt/drjavanbot/current symlink switch
  -> production full reindex (last-known-good DB preserved by indexer)
  -> restart only drjavanbot.service
  -> post-start health/smoke
  -> on any failure: restore previous symlink + SQLite backup + restart old release
```

The Telegram process never runs sudo, git, pip or an arbitrary shell command. It can only write a schema-validated `update` or `rollback` request. Repository URL, branch, service name and deployment paths are hard-coded in the root-owned updater.

## Isolation

- control repository: `/opt/drjavanbot/app`
- immutable-ish tested releases: `/opt/drjavanbot/releases/<commit-sha>`
- active atomic symlink: `/opt/drjavanbot/current`
- each release has its own `.venv`
- persistent DB/state/secrets: `/var/lib/drjavanbot`
- cache: `/var/cache/drjavanbot`
- request/result/history: `/var/lib/drjavanbot/update`
- bot service: `drjavanbot.service`
- updater: `drjavanbot-updater.service` + `drjavanbot-updater.path`

Nothing references or restarts other Telegram bots on the host.

## Two test gates

1. GitHub Actions runs compile + full pytest on every push to `main`.
2. The server updater independently reruns compile + full pytest, then indexes the complete archive in an isolated staging data directory before touching production.

The server remains authoritative: a GitHub-green commit is not deployed unless server-side tests also pass.

## Archive updates

New or replaced `messages*.html` files committed to the repository are included in the candidate release. The updater builds a fresh staging index from that candidate archive, so malformed/new Telegram exports fail before the active bot is stopped. After a successful code switch the production index is rebuilt atomically.

## Rollback

The updater keeps the last healthy releases (default 3) and a release history. Owner rollback switches to the previous healthy release, rebuilds the archive index with that release, restarts only DrJavanBot, and restores the pre-operation SQLite backup if rollback validation itself fails.

## One-time bootstrap

This part still requires server/root access once:

```bash
cd /opt/drjavanbot/app
git pull --ff-only origin main
sudo python3 deploy/bootstrap_self_update.py
systemctl status drjavanbot.service --no-pager
systemctl status drjavanbot-updater.path --no-pager
```

Bootstrap creates the initial release-specific venv, points `current` at the tested release, preserves existing secrets, rewrites only `DRJAVAN_ARCHIVE_DIR` to the `current` symlink, installs the isolated systemd units and restarts only `drjavanbot.service`.

After bootstrap, routine workflow is simply: ChatGPT changes GitHub -> CI -> Owner presses Software Update -> server tests -> atomic deployment.
