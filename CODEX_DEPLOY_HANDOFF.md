# Codex deployment handoff
Repository: `ArianGhsm/DrJavanGroupDatabase`. Operational scope only; do not redesign/refactor.
1. Confirm repo/Stage-5 commit.
2. Use isolated `drjavanbot` layout from DEPLOYMENT.md.
3. Set only Telegram bot token + numeric owner ID and documented non-secret paths. Never request/set AvalAI key.
4. Install locks/package; run `pytest -q`.
5. Run `drjavanbot reindex`, `drjavanbot health`, `drjavanbot-smoke`, `drjavanbot-smoke --telegram`.
6. Start systemd unit; report exact exit codes, test count, index stats, smoke and service/journal status with secrets redacted.
7. If a code bug appears, stop and report exact trace/file/line; do not broad-refactor.
