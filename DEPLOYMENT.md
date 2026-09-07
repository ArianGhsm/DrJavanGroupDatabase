# DrJavanBot production deployment

Deployment target is an isolated Linux/systemd service named `drjavanbot`. Do not share its Unix user, app directory, virtualenv, env file, runtime DB, cache, secret directory or process name with other bots.

## Fixed isolated layout

- Unix user/group: `drjavanbot`
- app: `/opt/drjavanbot/app`
- venv: `/opt/drjavanbot/venv`
- env: `/etc/drjavanbot/drjavanbot.env` (0600)
- data/SQLite: `/var/lib/drjavanbot/data`
- AvalAI SecretStore: `/var/lib/drjavanbot/secrets` (directory 0700; secret file 0600)
- cache: `/var/cache/drjavanbot`
- logs: journald, `journalctl -u drjavanbot`
- service: `/etc/systemd/system/drjavanbot.service`

Prerequisites: Linux/systemd, Python 3.11+, Git, SQLite with FTS5, outbound HTTPS/DNS to Telegram and AvalAI.

## 1. Clone or update code

Fresh install:

```bash
sudo install -d -m 0755 /opt/drjavanbot
sudo git clone https://github.com/ArianGhsm/DrJavanGroupDatabase.git /opt/drjavanbot/app
cd /opt/drjavanbot/app
git rev-parse HEAD
```

Existing install:

```bash
cd /opt/drjavanbot/app
git fetch origin
git checkout main
git pull --ff-only origin main
git rev-parse HEAD
```

The checked-out SHA must be the Stage-5 handoff SHA supplied with deployment.

## 2. Create isolated user/runtime directories

```bash
sudo useradd --system --home /var/lib/drjavanbot --shell /usr/sbin/nologin drjavanbot || true
sudo install -d -o drjavanbot -g drjavanbot -m 0700 \
  /var/lib/drjavanbot /var/lib/drjavanbot/data /var/lib/drjavanbot/secrets /var/cache/drjavanbot
sudo install -d -o root -g root -m 0755 /etc/drjavanbot
```

## 3. Install exact runtime/test dependencies

```bash
python3.11 -m venv /opt/drjavanbot/venv
cd /opt/drjavanbot/app
/opt/drjavanbot/venv/bin/python -m pip install -r requirements.lock
/opt/drjavanbot/venv/bin/python -m pip install --no-deps .
/opt/drjavanbot/venv/bin/python -m pip install -r requirements-dev.lock
```

Expected: all commands exit 0 and `drjavanbot`, `drjavanbot-bot`, `drjavanbot-smoke` exist in `/opt/drjavanbot/venv/bin/`.

## 4. Create runtime env — no AvalAI key

Create `/etc/drjavanbot/drjavanbot.env` with mode 0600. Required secret values for deployment are only the Telegram Bot Token and numeric Owner ID:

```text
TELEGRAM_BOT_TOKEN=<set on server only>
TELEGRAM_OWNER_ID=<numeric Telegram user id>
DRJAVAN_ARCHIVE_DIR=/opt/drjavanbot/app/گروه دکتر جوان
DRJAVAN_DATA_DIR=/var/lib/drjavanbot/data
DRJAVAN_CACHE_DIR=/var/cache/drjavanbot
AVALAI_BASE_URL=https://api.avalai.ir/v1
AVALAI_MODEL=deepseek-v4-flash
LOG_LEVEL=INFO
```

Then:

```bash
sudo chmod 0600 /etc/drjavanbot/drjavanbot.env
sudo chown root:root /etc/drjavanbot/drjavanbot.env
```

**Never put `AVALAI_API_KEY` in this env or Git.** The owner configures/replaces/removes it later from the bot's private Telegram `/settings`; SecretStore writes it under `/var/lib/drjavanbot/secrets/`.

## 5. Run complete tests and build the real archive index

```bash
cd /opt/drjavanbot/app
set -a; . /etc/drjavanbot/drjavanbot.env; set +a
/opt/drjavanbot/venv/bin/pytest -q
/opt/drjavanbot/venv/bin/drjavanbot reindex
/opt/drjavanbot/venv/bin/drjavanbot health
```

Expected:
- `pytest`: exit 0; report exact test count.
- `reindex`: exit 0; report actual archive file/message counts and elapsed time.
- `health`: index `healthy=true`, SQLite/FTS integrity clean.

Do not continue to service startup if any of these fail. Report the exact error/trace instead of broad code changes.

## 6. Smoke tests

Local/index smoke (no network credential beyond loaded env needed):

```bash
/opt/drjavanbot/venv/bin/drjavanbot-smoke
```

Expected: `healthy: true`, `fts5: true`, archive exists, index healthy.

Telegram credential/network smoke:

```bash
/opt/drjavanbot/venv/bin/drjavanbot-smoke --telegram
```

Expected: exit 0 and Telegram `getMe` returns a bot id. This does not require or test AvalAI API Key.

## 7. Install/start isolated systemd service

```bash
sudo cp /opt/drjavanbot/app/deploy/drjavanbot.service /etc/systemd/system/drjavanbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now drjavanbot
sudo systemctl status drjavanbot --no-pager
sudo journalctl -u drjavanbot -n 100 --no-pager
```

Expected: service `active (running)`; no traceback, token, API key or secret value in logs.

## 8. Owner completes AvalAI setup in Telegram

In a private chat with the running bot, owner opens `/settings`, selects Set/Replace API Key, sends the AvalAI key, and uses Test AvalAI. The key message is deleted best-effort, candidate key is validated before atomic replacement, and no restart is required.

## Stop / restart / rollback

```bash
sudo systemctl stop drjavanbot
sudo systemctl restart drjavanbot
sudo systemctl disable --now drjavanbot   # only when intentionally disabling
```

Rollback code without deleting runtime state/secrets:

```bash
sudo systemctl stop drjavanbot
cd /opt/drjavanbot/app
git checkout <previous-known-good-sha>
/opt/drjavanbot/venv/bin/python -m pip install --no-deps .
sudo systemctl start drjavanbot
sudo journalctl -u drjavanbot -n 100 --no-pager
```

Do not delete `/var/lib/drjavanbot` during rollback. Full reindex builds a temporary database and preserves the previous known-good index unless the replacement passes integrity validation.
