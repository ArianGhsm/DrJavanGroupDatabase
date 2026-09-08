from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_bootstrap():
    path = ROOT / "deploy" / "bootstrap_self_update.py"
    spec = importlib.util.spec_from_file_location("drjavan_bootstrap_ordering_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_bootstrap_repairs_legacy_env_before_release_prepare_and_tests(tmp_path, monkeypatch):
    module = _load_bootstrap()
    release = tmp_path / "release"
    release.mkdir()
    sha = "a" * 40
    events: list[str] = []

    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "LOCK_FILE", tmp_path / "updater.lock")
    monkeypatch.setattr(
        module,
        "_preflight",
        lambda: (SimpleNamespace(pw_uid=1000, pw_gid=1000), "/usr/bin/python3", sha),
    )
    monkeypatch.setattr(module, "_rewrite_env_archive_path", lambda: events.append("repair_env"))
    monkeypatch.setattr(
        module,
        "_prepare_release",
        lambda resolved_sha, python_exe: (events.append("prepare_release") or release),
    )
    monkeypatch.setattr(module, "_run_release_tests", lambda path: events.append("run_tests"))
    monkeypatch.setattr(module, "_make_release_runtime_readable", lambda path: events.append("publish_release"))
    monkeypatch.setattr(module, "_active_release", lambda: None)
    monkeypatch.setattr(module, "_snapshot_units", lambda: {})
    monkeypatch.setattr(module, "_switch_current", lambda path: events.append("switch_current"))
    monkeypatch.setattr(module, "_prepare_update_state", lambda *args: events.append("update_state"))
    monkeypatch.setattr(module, "_install_units", lambda: events.append("install_units"))
    monkeypatch.setattr(module, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_wait_service_active", lambda: events.append("service_active"))
    monkeypatch.setattr(
        module,
        "_record_bootstrap_success",
        lambda *args, **kwargs: events.append("record_success"),
    )

    assert module.main() == 0
    assert events[:4] == ["repair_env", "prepare_release", "run_tests", "publish_release"]
    assert events.index("publish_release") < events.index("switch_current")
    assert events.index("service_active") < events.index("record_success")
