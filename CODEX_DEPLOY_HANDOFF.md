# Codex deployment handoff
Repository: `ArianGhsm/DrJavanGroupDatabase`. Operational scope only; do not redesign/refactor.

For an existing running DrJavanBot, this is now a one-time self-updater bootstrap:

1. Confirm repository is exactly `ArianGhsm/DrJavanGroupDatabase` and do not touch any other bot/service.
2. In `/opt/drjavanbot/app`, run `git pull --ff-only origin main`.
3. Run the repository test suite. If any test fails, stop and report exact command/trace/file/line; do not refactor.
4. Run `sudo python3 deploy/bootstrap_self_update.py`.
5. Verify only these units: `drjavanbot.service`, `drjavanbot-updater.path`, `drjavanbot-updater.service`.
6. Verify `drjavanbot.service` is active and `drjavanbot-updater.path` is active/waiting.
7. Send one normal Telegram question and verify the internal-error regression is gone.
8. Do not request, print, move or replace the AvalAI key; existing runtime secret is preserved.
9. Do not stop/restart/modify any other Telegram bot on the server.

After this bootstrap, routine updates are performed by the Owner-only Telegram `Software Update` button. Codex is no longer required for normal GitHub changes.
