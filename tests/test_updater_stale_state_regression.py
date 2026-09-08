from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_bootstrap():
    path = ROOT / "deploy" / "bootstrap_self_update.py"
    spec = importlib.util.spec_from_file_location("bootstrap_stale_state_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_bootstrap_success_supersedes_legacy_failure_and_clears_stale_ui_state(tmp_path, monkeypatch):
    module = _load_bootstrap()
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    old_sha = "a" * 40
    new_sha = "b" * 40
    previous = tmp_path / old_sha
    previous.mkdir()
    (previous / ".deploy_commit").write_text(old_sha + "\n", encoding="utf-8")
    (update_dir / "result.json").write_text(json.dumps({"schema": 1, "state": "failed", "current_sha": old_sha}), encoding="utf-8")
    (update_dir / "request.json").write_text("{}", encoding="utf-8")
    (update_dir / "progress-ui.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "UPDATE_DIR", update_dir)
    monkeypatch.setattr(module.os, "chown", lambda *args, **kwargs: None)
    account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())

    module._record_bootstrap_success(account, new_sha, previous, started_at="2026-09-08T13:00:00+00:00")

    result = json.loads((update_dir / "result.json").read_text(encoding="utf-8"))
    assert result["schema"] == 2
    assert result["state"] == "success"
    assert result["current_sha"] == old_sha
    assert result["target_sha"] == new_sha
    assert result["progress_current"] == result["progress_total"] == 7
    assert not (update_dir / "request.json").exists()
    assert not (update_dir / "progress-ui.json").exists()


def test_bot_service_repairs_update_directory_before_start():
    unit = (ROOT / "deploy" / "drjavanbot.service").read_text(encoding="utf-8")
    repair = "ExecStartPre=+/usr/bin/install -d -o drjavanbot -g drjavanbot -m 0700 /var/lib/drjavanbot/update"
    assert repair in unit
    assert unit.index(repair) < unit.index("ExecStart=/opt/drjavanbot/current/.venv/bin/drjavanbot-bot")
