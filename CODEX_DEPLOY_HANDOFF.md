# Codex deployment handoff

Repository: `ArianGhsm/DrJavanGroupDatabase`. Operational scope only; do not redesign/refactor and do not touch any other bot/service.

For the existing running DrJavanBot, this is the final one-time self-updater bootstrap/repair:

1. Verify repository is exactly `ArianGhsm/DrJavanGroupDatabase`.
2. In `/opt/drjavanbot/app`, run `git fetch origin main` and `git pull --ff-only origin main`.
3. Confirm the tracked working tree is clean.
4. Run `pytest -q`. If any test fails, stop and report exact command/exit code/trace; do not modify code.
5. Run `sudo python3 deploy/bootstrap_self_update.py`.
6. Verify only:
   - `drjavanbot.service` = active;
   - `drjavanbot-updater.path` = active/waiting;
   - `drjavanbot-updater.service` may be inactive between requests because it is oneshot.
7. For a manual smoke, source `/etc/drjavanbot/drjavanbot.env` only after bootstrap has rewritten the archive path to the quoted `current` path, then run `/opt/drjavanbot/current/.venv/bin/drjavanbot-smoke --telegram`.
8. Send one normal Telegram question and verify the internal-error regression is gone.
9. Do not request, print, move or replace the AvalAI key; existing runtime secret is preserved.
10. Do not stop/restart/modify any other Telegram bot on the server.

If bootstrap or smoke fails, report the precise failure and stop. Do not use `sed`, ad-hoc env rewrites, `git reset --hard`, broad permission changes, or server-side refactors to bypass a failed gate.

After a successful bootstrap, routine code/archive updates are performed only by the Owner-only Telegram **Software Update** flow. Codex is not required for normal GitHub changes.
