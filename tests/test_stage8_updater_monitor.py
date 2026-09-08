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
    # self_update.py is now only the stable systemd compatibility entrypoint;
    # operational behavior is owned by the v2 engine and is tested directly.
    if filename == "self_update.py":
        filename = "update_engine_v2.py"
    path = ROOT / "deploy" / filename
    spec = importlib.util.spec_from_file_location(f"stage8_{filename.replace('.', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_update_status_prefers_live_structured_progress(tmp_path):
    control = UpdateControl(tmp_path / "data")
    request_id = control.request("update", target_sha="b" * 40, ci_status="success")
    control.result_path.write_text(json.dumps({
        "schema": 2,
        "state": "preparing",
        "stage": "preparing",
        "stage_label": "آماده‌سازی نسخه",
        "progress_current": 3,
        "progress_total": 7,
        "request_id": request_id,
        "action": "update",
        "current_sha": "a" * 40,
        "target_sha": "b" * 40,
        "message": "candidate در حال آماده‌سازی است",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    status = control.status()
    assert status.state == "preparing"
    assert status.stage_label == "آماده‌سازی نسخه"
    assert (status.progress_current, status.progress_total) == (3, 7)
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


def test_github_update_check_includes_exact_sha_ci_and_notification_dedup(tmp_path, monkeypatch):
    import drjavanbot.telegram.update_control as module

    marker = tmp_path / ".deploy_commit"
    marker.write_text("a" * 40 + "\n", encoding="utf-8")
    monkeypatch.setattr(module, "_CURRENT_MARKER", marker)

    class Response:
        status = 200
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        if "actions/runs" in request.full_url:
            return Response({"workflow_runs": [{
                "head_sha": "b" * 40,
                "name": "DrJavanBot tests",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-08T10:00:00Z",
                "html_url": "https://github.com/ArianGhsm/DrJavanGroupDatabase/actions/runs/1",
            }]})
        return Response({"sha": "b" * 40})

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    control = UpdateControl(tmp_path / "data")
    info = control.remote_version(timeout=1)
    assert info is not None and info.update_available and info.installable
    assert info.current_sha == "a" * 40 and info.latest_sha == "b" * 40
    assert info.ci_status == "success"
    assert control.claim_update_notification(info.latest_sha, "available:success:notify") is True
    assert control.claim_update_notification(info.latest_sha, "available:success:notify") is False


def test_default_update_check_and_live_progress_intervals_are_bounded():
    assert TelegramConfig().update_check_interval_seconds == 300
    assert TelegramConfig().update_progress_interval_seconds == 2


class FakeAPI:
    def __init__(self): self.sent = []
    def send_message(self, chat_id, text, **kwargs): self.sent.append((chat_id, str(text), kwargs)); return {"message_id": len(self.sent)}


class FakeState:
    def access_mode(self): return "owner_only"


class FakeServices:
    def __init__(self): self.claimed = []
    def remote_update_info(self): return SimpleNamespace(current_sha="a" * 40, latest_sha="b" * 40, update_available=True, ci_status="success")
    def claim_update_notification(self, sha, kind="available"): self.claimed.append((sha, kind)); return True
    def ai_configured(self): return True
    def model(self): return "deepseek-v4-flash"


def test_owner_start_opens_new_control_center_and_notifies_green_update():
    api = FakeAPI(); services = FakeServices()
    app = TelegramBotApp(api=api, owner_id=42, services=services, state=FakeState(), config=TelegramConfig())
    app._handle_command(42, 42, True, "/start")
    assert len(api.sent) == 2
    assert "مرکز مدیریت" in api.sent[0][1]
    assert "نسخه جدید" in api.sent[1][1]
    home_keyboard = api.sent[0][2]["reply_markup"]["inline_keyboard"]
    assert any(button["callback_data"] == "adm:update" for row in home_keyboard for button in row)
    keyboard = api.sent[1][2]["reply_markup"]["inline_keyboard"]
    assert any(button["callback_data"] == "adm:update:install" for row in keyboard for button in row)


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


def test_staged_sqlite_is_promoted_only_for_index_paths(tmp_path, monkeypatch):
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
    source = (ROOT / "deploy/update_engine_v2.py").read_text(encoding="utf-8")
    update_body = source.split("def _update", 1)[1].split("def _rollback", 1)[0]
    assert "_promote_sqlite(stage_db, db_path)" in update_body
    assert "if stage_db is not None" in update_body
    assert 'CURRENT / ".venv/bin/drjavanbot"), "reindex"' not in update_body


def test_bootstrap_serializes_with_path_triggered_updater():
    body = (ROOT / "deploy/bootstrap_self_update.py").read_text(encoding="utf-8")
    assert 'LOCK_FILE = Path("/run/lock/drjavanbot-updater.lock")' in body
    assert "fcntl.flock(lock.fileno(), fcntl.LOCK_EX)" in body


def test_bootstrap_publishes_release_before_switch():
    source = (ROOT / "deploy/bootstrap_self_update.py").read_text(encoding="utf-8")
    main_body = source.split("def main()", 1)[1].split("def _preflight", 1)[0]
    assert main_body.index("_make_release_runtime_readable(release)") < main_body.index("_switch_current(release)")
