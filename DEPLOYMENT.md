# DrJavanBot production deployment

Use a dedicated Unix user/service `drjavanbot`. Fixed isolation: app `/opt/drjavanbot/app`, venv `/opt/drjavanbot/venv`, env `/etc/drjavanbot/drjavanbot.env`, state/secrets `/var/lib/drjavanbot`, cache `/var/cache/drjavanbot`, logs via `journalctl -u drjavanbot`. No path/process/env/venv is shared with other bots.

Prerequisites: Linux/systemd, Python 3.11+, SQLite FTS5, outbound HTTPS.

```bash
sudo useradd --system --home /var/lib/drjavanbot --shell /usr/sbin/nologin drjavanbot || true
sudo install -d -o drjavanbot -g drjavanbot -m 0700 /var/lib/drjavanbot /var/lib/drjavanbot/data /var/lib/drjavanbot/secrets /var/cache/drjavanbot
python3.11 -m venv /opt/drjavanbot/venv
cd /opt/drjavanbot/app
/opt/drjavanbot/venv/bin/pip install -r requirements.lock
/opt/drjavanbot/venv/bin/pip install --no-deps .
/opt/drjavanbot/venv/bin/pip install -r requirements-dev.lock
pytest -q
```

Create `/etc/drjavanbot/drjavanbot.env` mode 0600 with `TELEGRAM_BOT_TOKEN`, numeric `TELEGRAM_OWNER_ID`, `DRJAVAN_ARCHIVE_DIR=/opt/drjavanbot/app/گروه دکتر جوان`, `DRJAVAN_DATA_DIR=/var/lib/drjavanbot/data`, `DRJAVAN_CACHE_DIR=/var/cache/drjavanbot`, `AVALAI_BASE_URL=https://api.avalai.ir/v1`, `AVALAI_MODEL=deepseek-v4-flash`, `LOG_LEVEL=INFO`. Do not put AvalAI API key in env; owner configures it later through private Telegram `/settings` and it is stored under `/var/lib/drjavanbot/secrets/`.

```bash
set -a; . /etc/drjavanbot/drjavanbot.env; set +a
drjavanbot reindex
drjavanbot health
drjavanbot-smoke
drjavanbot-smoke --telegram
sudo cp deploy/drjavanbot.service /etc/systemd/system/drjavanbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now drjavanbot
sudo systemctl status drjavanbot --no-pager
sudo journalctl -u drjavanbot -n 100 --no-pager
```

Rollback: stop service, checkout previous known-good commit, reinstall package, restart. Do not delete runtime DB/cache/secrets. Full reindex preserves last-known-good DB until the new temporary DB passes integrity checks.
