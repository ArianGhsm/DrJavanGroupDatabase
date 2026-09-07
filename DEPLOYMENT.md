# DrJavanBot production deployment

DrJavanBot must remain isolated from every other bot on the host. The self-updater architecture uses a control repository plus immutable release worktrees; persistent data and secrets never live inside a release.

## Fixed isolated layout

- Unix user/group: `drjavanbot`
- control repository: `/opt/drjavanbot/app`
- releases: `/opt/drjavanbot/releases/<commit-sha>`
- active symlink: `/opt/drjavanbot/current`
- per-release virtualenv: `/opt/drjavanbot/releases/<commit-sha>/.venv`
- env: `/etc/drjavanbot/drjavanbot.env` (`0600`, `root:root`)
- data/SQLite: `/var/lib/drjavanbot/data`
- AvalAI SecretStore: `/var/lib/drjavanbot/secrets` (`0700`; secret files `0600`)
- updater state: `/var/lib/drjavanbot/update`
- cache: `/var/cache/drjavanbot`
- logs: journald (`journalctl -u drjavanbot`)
- bot service: `drjavanbot.service`
- updater units: `drjavanbot-updater.path` + `drjavanbot-updater.service`

Prerequisites: Linux/systemd, Python 3.11+, Git, SQLite with FTS5, outbound HTTPS/DNS to Telegram, GitHub and AvalAI.

## Runtime env format

`/etc/drjavanbot/drjavanbot.env` is intentionally compatible with both systemd `EnvironmentFile=` parsing and shell sourcing for operational checks. **Any value containing whitespace must be quoted.**

```text
TELEGRAM_BOT_TOKEN=<server-only token>
TELEGRAM_OWNER_ID=<numeric Telegram user id>
DRJAVAN_ARCHIVE_DIR="/opt/drjavanbot/current/گروه دکتر جوان"
DRJAVAN_DATA_DIR=/var/lib/drjavanbot/data
DRJAVAN_CACHE_DIR=/var/cache/drjavanbot
AVALAI_BASE_URL=https://api.avalai.ir/v1
AVALAI_MODEL=deepseek-v4-flash
LOG_LEVEL=INFO
```

Never put `AVALAI_API_KEY` in this file or Git. The owner configures it through the private Telegram settings flow; SecretStore keeps it under `/var/lib/drjavanbot/secrets/`.

Permissions:

```bash
sudo chmod 0600 /etc/drjavanbot/drjavanbot.env
sudo chown root:root /etc/drjavanbot/drjavanbot.env
```

## Existing running installation: one-time self-updater bootstrap

This is the normal path for the current server.

**Important:** do not source the existing runtime env before bootstrap. Older installations may still contain the historical unquoted Persian archive path. Repository pytest does not need runtime secrets or the production env.

```bash
cd /opt/drjavanbot/app
git fetch origin main
git pull --ff-only origin main
pytest -q
sudo python3 deploy/bootstrap_self_update.py
```

At the start of bootstrap, after repository/user/prerequisite preflight and before release preparation/tests, the bootstrap atomically repairs `DRJAVAN_ARCHIVE_DIR` to the quoted `current` path while preserving every other env line plus the file owner/mode. That repair is intentionally retained even if a later bootstrap gate fails.

Bootstrap is idempotent and performs these checks before switching the live service:

- repository origin is exactly the expected HTTPS GitHub repository;
- tracked working tree is clean;
- Python is 3.11+ and SQLite FTS5 works;
- archive, env file and systemd unit sources exist;
- legacy archive env entry is repaired before release tests;
- release worktree really points to the requested commit;
- release dependencies are reconciled even after an interrupted prior attempt;
- compile + full pytest run with a private temporary directory and explicit pytest `--basetemp`;
- existing release history is preserved;
- only DrJavanBot systemd units are installed/restarted.

If bootstrap fails after changing the active symlink/unit files, it restores the prior active release/unit snapshot where available and restarts only `drjavanbot.service`.

Verify:

```bash
systemctl is-active drjavanbot.service
systemctl is-active drjavanbot-updater.path
systemctl status drjavanbot-updater.path --no-pager
```

Expected:

- `drjavanbot.service`: `active`
- `drjavanbot-updater.path`: `active (waiting)`
- `drjavanbot-updater.service`: normally `inactive` between update requests because it is a oneshot service

## Safe manual smoke without editing env

Only after successful bootstrap, the repaired env can safely be sourced for manual checks:

```bash
set -a
. /etc/drjavanbot/drjavanbot.env
set +a
/opt/drjavanbot/current/.venv/bin/drjavanbot-smoke
/opt/drjavanbot/current/.venv/bin/drjavanbot-smoke --telegram
```

Never print or echo the token while doing this.

## Routine updates after bootstrap

Routine updates no longer require Codex or SSH:

1. ChatGPT changes and commits GitHub `main`.
2. GitHub Actions runs compile + full pytest.
3. Owner opens Telegram `/settings` → **Software Update**.
4. The bot writes a fixed-schema update request.
5. Root-owned updater fetches `origin/main` and creates `/opt/drjavanbot/releases/<sha>`.
6. Candidate release gets its own virtualenv.
7. Server reruns compile + pytest in an isolated temp tree.
8. A complete archive index is built in isolated staging data.
9. Health/local smoke/Telegram `getMe` must pass.
10. Only then is `drjavanbot.service` stopped, SQLite backed up, `current` atomically switched, production reindexed and the bot restarted.
11. If post-switch validation fails, previous release and SQLite state are restored.

The updater never accepts a repository URL, branch, service name, filesystem path or shell command from Telegram.

## Rollback

Owner rollback from Telegram selects the previous healthy release from updater history. It does not delete persistent secrets or runtime state. Rollback also reindexes and validates before being considered successful.

Do not use ad-hoc `git checkout` against the live bot after the release architecture is installed.

## Failure reporting contract

If any bootstrap/update/smoke step fails, stop and report:

- exact command/operation;
- exit code;
- exception/error text with secrets redacted;
- current release SHA and target SHA where available.

Do not broad-refactor on the server and do not modify/restart any other bot.

## Fresh installation notes

A fresh install still requires creating the isolated user/directories and initial env file before running the bootstrap:

```bash
sudo useradd --system --home /var/lib/drjavanbot --shell /usr/sbin/nologin drjavanbot || true
sudo install -d -m 0755 /opt/drjavanbot
sudo install -d -o drjavanbot -g drjavanbot -m 0700 \
  /var/lib/drjavanbot /var/lib/drjavanbot/data /var/lib/drjavanbot/secrets \
  /var/lib/drjavanbot/update /var/cache/drjavanbot
sudo install -d -o root -g root -m 0755 /etc/drjavanbot
sudo git clone https://github.com/ArianGhsm/DrJavanGroupDatabase.git /opt/drjavanbot/app
```

Create the env file using the quoted archive path shown above, then run `sudo python3 deploy/bootstrap_self_update.py`.
