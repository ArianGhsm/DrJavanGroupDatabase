from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from drjavanbot.telegram.update_control import UpdateControl


ROOT = Path(__file__).resolve().parents[1]


def load_engine():
    path = ROOT / "deploy" / "update_engine_v2.py"
    spec = importlib.util.spec_from_file_location("drjavan_update_engine_v2_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_change_classifier_keeps_code_only_fast_path():
    engine = load_engine()
    assert engine._classify_changes(("src/drjavanbot/telegram/admin_ui.py",), current_sha="a" * 40) == "code_only"
    assert engine._classify_changes(("requirements.lock",), current_sha="a" * 40) == "dependency"
    assert engine._classify_changes(("src/drjavanbot/search/backend.py",), current_sha="a" * 40) == "index"
    assert engine._classify_changes(("گروه دکتر جوان/messages247.html",), current_sha="a" * 40) == "archive"
    assert engine._classify_changes(("requirements.lock", "گروه دکتر جوان/messages247.html"), current_sha="a" * 40) == "mixed"


def test_code_only_stage_gate_never_reindexes_or_runs_pytest(tmp_path, monkeypatch):
    engine = load_engine()
    release = tmp_path / "release"; release.mkdir()
    stage = tmp_path / "stage"
    calls = []
    monkeypatch.setattr(engine, "_run", lambda cmd, **kwargs: calls.append((list(cmd), kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    result = engine._run_stage_gates(
        release, stage,
        {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_ID": "42", "DRJAVAN_DATA_DIR": "/prod/data", "DRJAVAN_CACHE_DIR": "/prod/cache"},
        change_class="code_only",
    )
    assert result is None
    flattened = [item for cmd, _ in calls for item in cmd]
    assert "reindex" not in flattened
    assert "pytest" not in flattened
    assert "health" in flattened
    assert any("drjavanbot-smoke" in item for item in flattened)


def test_index_change_builds_candidate_database_before_switch(tmp_path, monkeypatch):
    engine = load_engine()
    release = tmp_path / "release"; release.mkdir()
    stage = tmp_path / "stage"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        if "reindex" in cmd:
            db = stage / "data" / "archive.sqlite3"
            db.parent.mkdir(parents=True, exist_ok=True)
            db.touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine, "_run", fake_run)
    result = engine._run_stage_gates(
        release, stage,
        {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_ID": "42", "DRJAVAN_DATA_DIR": "/prod/data", "DRJAVAN_CACHE_DIR": "/prod/cache"},
        change_class="index",
    )
    assert result == stage / "data" / "archive.sqlite3"
    assert any("reindex" in cmd for cmd, _ in calls)
    assert not any("pytest" in cmd for cmd, _ in calls)
    reindex_env = next(kwargs["env"] for cmd, kwargs in calls if "reindex" in cmd)
    assert reindex_env["TMPDIR"] == str(stage / "tmp")
    assert reindex_env["DRJAVAN_DATA_DIR"] == str(stage / "data")


def test_privileged_engine_requires_exact_green_ci(monkeypatch):
    engine = load_engine()

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({"workflow_runs": [{
                "head_sha": "b" * 40,
                "name": engine.EXPECTED_WORKFLOW,
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-08T10:00:00Z",
            }]}).encode()

    monkeypatch.setattr(engine, "urlopen", lambda request, timeout: Response())
    assert engine._github_ci_status("b" * 40, timeout=1) == "success"


def test_privileged_engine_fails_closed_for_pending_or_unknown_ci(monkeypatch):
    engine = load_engine()

    class Pending:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({"workflow_runs": [{
                "head_sha": "b" * 40,
                "name": engine.EXPECTED_WORKFLOW,
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-09-08T10:00:00Z",
            }]}).encode()

    monkeypatch.setattr(engine, "urlopen", lambda request, timeout: Pending())
    assert engine._github_ci_status("b" * 40, timeout=1) == "pending"
    monkeypatch.setattr(engine, "urlopen", lambda request, timeout: (_ for _ in ()).throw(OSError("offline")))
    assert engine._github_ci_status("b" * 40, timeout=1) == "unknown"


def test_update_control_request_preserves_old_schema_but_pins_verified_target(tmp_path):
    control = UpdateControl(tmp_path / "data")
    request_id = control.request("update", target_sha="b" * 40, ci_status="success", source="auto")
    payload = json.loads(control.request_path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["request_id"] == request_id
    assert payload["target_sha"] == "b" * 40
    assert payload["ci_status"] == "success"
    assert payload["source"] == "auto"


def test_update_control_notification_is_deduped_per_sha_and_event(tmp_path):
    control = UpdateControl(tmp_path / "data")
    sha = "b" * 40
    assert control.claim_update_notification(sha, "available:success:notify") is True
    assert control.claim_update_notification(sha, "available:success:notify") is False
    assert control.claim_update_notification(sha, "failed") is True


def test_progress_binding_is_structured_and_bounded(tmp_path):
    control = UpdateControl(tmp_path / "data")
    control.bind_progress_message("req", 42, 99, target_sha="b" * 40)
    binding = control.progress_binding()
    assert binding is not None
    assert (binding.request_id, binding.chat_id, binding.message_id, binding.target_sha) == ("req", 42, 99, "b" * 40)
    control.clear_progress_binding("req")
    assert control.progress_binding() is None


def test_engine_is_hard_locked_to_drjavan_service_and_repo():
    engine = load_engine()
    assert engine.SERVICE == "drjavanbot.service"
    assert engine.EXPECTED_REPO == "ArianGhsm/DrJavanGroupDatabase"
    assert all(remote.startswith("https://github.com/ArianGhsm/DrJavanGroupDatabase") for remote in engine.ALLOWED_REMOTES)
    source = (ROOT / "deploy" / "update_engine_v2.py").read_text(encoding="utf-8")
    assert '"reset", "--hard"' not in source
    assert "Dent1402" not in source and "VoiceMatn" not in source


def test_production_engine_does_not_repeat_ci_quality_suite():
    source = (ROOT / "deploy" / "update_engine_v2.py").read_text(encoding="utf-8")
    stage = source[source.index("def _run_stage_gates"):source.index("def _github_ci_status")]
    assert "pytest" not in stage
    assert "quality-eval" not in stage
    assert "Full Archive Quality Lab" not in stage
    assert "health" in stage
    assert "drjavanbot-smoke" in stage


def test_structured_result_has_real_stage_progress_and_error_fields():
    engine = load_engine()
    result = engine._result(
        "preparing", "req", "update", "a" * 40, "b" * 40, "در حال آماده‌سازی",
        stage="preparing", stage_label="آماده‌سازی نسخه", progress_current=3,
        progress_total=7, started_at="2026-09-08T10:00:00+00:00", ci_status="success",
        change_class="code_only",
    )
    assert result["schema"] == 2
    assert result["stage"] == "preparing"
    assert result["progress_current"] == 3 and result["progress_total"] == 7
    assert result["ci_status"] == "success"
    assert result["change_class"] == "code_only"
