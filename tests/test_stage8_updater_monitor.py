from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from drjavanbot.telegram.app_v2 import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.update_control import UpdateControl

ROOT = Path(__file__).resolve().parents[1]


def _load_deploy(filename: str):
    path = ROOT / "deploy" / filename
    spec = importlib.util.spec_from_file_location(f"stage8_{filename.replace('.', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_update_status_prefers_live_running_progress(tmp_path):
    control = UpdateControl(tmp_path / "data")
    request_id = control.request("update")
    control.result_path.write_text(json.dumps({
        "schema": 1,
        "state": "running",
        "request_id": request_id,
        "action": "update",
        "current_sha": "a" * 40,
        "target_sha": "b" * 40,
        "message": "staging index",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    status = control.status()
    assert status.state == "running"
    assert status.message == "staging index"
    assert status.target_sha == "b" * 40


def test_stale_pending_request_is_recoverable(tmp_path):
    control = UpdateControl(tmp_path / "data")
    control.request_path.write_text(json.dumps({
        "schema": 1,
        "request_id": "old",
        "action": "update",
        "requested_at": (datetime.now(timezone.utc) - timedelta(minutes=46)).isoformat(),
    }), encoding="utf-8")
    assert control.status().state == "stalled"
    new_id = control.request("update")
    assert new_id != "old"
    assert json.loads(control.request_path.read_text(encoding="utf-8"))["request_id"] == new_id


def test_github_update_check_and_notification_dedup(tmp_path, monkeypatch):
    import drjavanbot.telegram.update_control as module

    marker = tmp_path / ".deploy_commit"
    marker.write_text("a" * 40 + "\n", encoding="utf-8")
    monkeypatch.setattr(module, "_CURRENT_MARKER", marker)

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({"sha": "b" * 40}).encode("utf-8")

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: Response())
    control = UpdateControl(tmp_path / "data")
    info = control.remote_version(timeout=1)
    assert info is not None and info.update_available
    assert info.current_sha == "a" * 40 and info.latest_sha == "b" * 40
    assert control.claim_update_notification(info.latest_sha) is True
    assert control.claim_update_notification(info.latest_sha) is False


def test_default_update_check_interval_is_five_minutes():
    assert TelegramConfig().update_check_interval_seconds == 300


class FakeAPI:
    def __init__(self): self.sent = []
    def send_message(self, chat_id, text, **kwargs): self.sent.append((chat_id, text, kwargs)); return {}


class FakeState:
    def access_mode(self): return "owner_only"


class FakeServices:
    def __init__(self): self.claimed = []
    def remote_update_info(self): return SimpleNamespace(current_sha="a" * 40, latest_sha="b" * 40, update_available=True)
    def claim_update_notification(self, sha): self.claimed.append(sha); return True
    def ai_configured(self): return True
    def model(self): return "deepseek-v4-flash"


def test_owner_start_checks_for_update_and_shows_install_button():
    api = FakeAPI(); services = FakeServices()
    app = TelegramBotApp(api=api, owner_id=42, services=services, state=FakeState(), config=TelegramConfig())
    app._handle_command(42, 42, True, "/start")
    assert len(api.sent) == 2
    assert "پنل مالک" in api.sent[0][1]
    assert "نسخه جدید" in api.sent[1][1]
    keyboard = api.sent[1][2]["reply_markup"]["inline_keyboard"]
    assert any(button["callback_data"] == "software_update_confirm" for row in keyboard for button in row)


def test_updater_permission_publish_makes_release_traversable(tmp_path, monkeypatch):
    module = _load_deploy("self_update.py")
    releases = tmp_path / "releases"; release = releases / ("a" * 40)
    bin_dir = release / ".venv" / "bin"; bin_dir.mkdir(parents=True)
    entry = bin_dir / "drjavanbot-bot"; entry.write_text("#!/bin/sh\n", encoding="utf-8")
    data = release / "module.py"; data.write_text("x=1\n", encoding="utf-8")
    os.chmod(releases, 0o700); os.chmod(release, 0o700); os.chmod(entry, 0o700); os.chmod(data, 0o600)
    monkeypatch.setattr(module, "RELEASES", releases)
    monkeypatch.setattr(module, "CURRENT", tmp_path / "current")
    monkeypatch.setattr(module.os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.os, "lchown", lambda *args, **kwargs: None)
    module._make_release_runtime_readable(release)
    assert (releases.stat().st_mode & 0o777) == 0o755
    assert (release.stat().st_mode & 0o777) == 0o755
    assert (entry.stat().st_mode & 0o777) == 0o755
    assert (data.stat().st_mode & 0o777) == 0o644


def test_staged_sqlite_is_promoted_without_second_production_reindex(tmp_path, monkeypatch):
    module = _load_deploy("self_update.py")
    src = tmp_path / "stage.sqlite3"; dst = tmp_path / "prod.sqlite3"
    with sqlite3.connect(src) as con:
        con.execute("CREATE TABLE marker(value TEXT)")
        con.execute("INSERT INTO marker VALUES ('new')")
    with sqlite3.connect(dst) as con:
        con.execute("CREATE TABLE marker(value TEXT)")
        con.execute("INSERT INTO marker VALUES ('old')")
    monkeypatch.setattr(module, "_service_ids", lambda: (os.getuid(), os.getgid()))
    module._promote_sqlite(src, dst)
    with sqlite3.connect(dst) as con:
        assert con.execute("SELECT value FROM marker").fetchone()[0] == "new"
    source = (ROOT / "deploy/self_update.py").read_text(encoding="utf-8")
    update_body = source.split("def _update", 1)[1].split("def _rollback", 1)[0]
    assert "_promote_sqlite(stage_db, db_path)" in update_body
    assert 'CURRENT / ".venv/bin/drjavanbot"), "reindex"' not in update_body


def test_bootstrap_publishes_release_before_switch():
    source = (ROOT / "deploy/bootstrap_self_update.py").read_text(encoding="utf-8")
    main_body = source.split("def main()", 1)[1].split("def _preflight", 1)[0]
    assert main_body.index("_make_release_runtime_readable(release)") < main_body.index("_switch_current(release)")
