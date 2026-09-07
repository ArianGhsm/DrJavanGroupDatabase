#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

CONTROL_REPO = Path("/opt/drjavanbot/app")
RELEASES = Path("/opt/drjavanbot/releases")
CURRENT = Path("/opt/drjavanbot/current")
ENV_FILE = Path("/etc/drjavanbot/drjavanbot.env")
STATE_ROOT = Path("/var/lib/drjavanbot")
UPDATE_DIR = STATE_ROOT / "update"
REQUEST_FILE = UPDATE_DIR / "request.json"
RESULT_FILE = UPDATE_DIR / "result.json"
HISTORY_FILE = UPDATE_DIR / "history.json"
LOCK_FILE = Path("/run/lock/drjavanbot-updater.lock")
SERVICE = "drjavanbot.service"
EXPECTED_REPO = "ArianGhsm/DrJavanGroupDatabase"
ALLOWED_REMOTES = {
    "https://github.com/ArianGhsm/DrJavanGroupDatabase.git",
    "https://github.com/ArianGhsm/DrJavanGroupDatabase",
}
MAX_RELEASES = 3


class UpdateFailure(RuntimeError):
    pass


def main() -> int:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(UPDATE_DIR, 0o700)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        request = _read_request()
        if request is None:
            return 0
        request_id = request["request_id"]
        action = request["action"]
        try:
            if action == "update":
                result = _update(request_id)
            elif action == "rollback":
                result = _rollback(request_id)
            else:
                raise UpdateFailure("unsupported action")
        except Exception as exc:
            result = _result(
                state="failed",
                request_id=request_id,
                action=action,
                current_sha=_current_sha(),
                target_sha=None,
                message=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            print(f"drjavanbot updater failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            REQUEST_FILE.unlink(missing_ok=True)
        _atomic_json(RESULT_FILE, result)
        return 0 if result["state"] in {"success", "up_to_date", "rolled_back"} else 1


def _update(request_id: str) -> dict:
    _verify_control_repo()
    env = _load_env()
    _validate_runtime_env(env)
    _run(["git", "-C", str(CONTROL_REPO), "fetch", "--prune", "origin", "main"], timeout=180)
    target = _run_text(["git", "-C", str(CONTROL_REPO), "rev-parse", "origin/main"]).strip()
    current = _current_sha()
    if current == target:
        return _result("up_to_date", request_id, "update", current, target, "نسخه فعال همین حالا آخرین main است.")

    if current:
        ancestry = subprocess.run(
            ["git", "-C", str(CONTROL_REPO), "merge-base", "--is-ancestor", current, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if ancestry.returncode != 0:
            raise UpdateFailure("origin/main is not a fast-forward descendant of the active release")

    release = _prepare_release(target)
    stage_root = UPDATE_DIR / f"stage-{target[:12]}"
    shutil.rmtree(stage_root, ignore_errors=True)
    try:
        _run_stage_gates(release, stage_root, env)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    previous = _active_release()
    db_path = Path(env["DRJAVAN_DATA_DIR"]) / "archive.sqlite3"
    backup = UPDATE_DIR / f"archive-before-{target[:12]}.sqlite3"
    db_existed_before = db_path.exists()

    _run(["systemctl", "stop", SERVICE], timeout=180)
    try:
        _backup_sqlite(db_path, backup)
        _switch_current(release)
        prod_env = dict(os.environ)
        prod_env.update(env)
        prod_env["DRJAVAN_ARCHIVE_DIR"] = str(CURRENT / "گروه دکتر جوان")
        _run([str(CURRENT / ".venv/bin/drjavanbot"), "reindex"], cwd=CURRENT, env=prod_env, timeout=900)
        _run(["systemctl", "start", SERVICE], timeout=180)
        _wait_active(stable_seconds=4)
        _run([str(CURRENT / ".venv/bin/drjavanbot-smoke")], cwd=CURRENT, env=prod_env, timeout=120)
        if env.get("TELEGRAM_BOT_TOKEN"):
            _run([str(CURRENT / ".venv/bin/drjavanbot-smoke"), "--telegram"], cwd=CURRENT, env=prod_env, timeout=120)
        _wait_active(stable_seconds=2)
    except Exception:
        _run(["systemctl", "stop", SERVICE], timeout=180, check=False)
        if previous is not None and previous.exists():
            _switch_current(previous)
        _restore_sqlite(backup, db_path, existed_before=db_existed_before)
        _run(["systemctl", "start", SERVICE], timeout=180, check=False)
        raise

    if current:
        _append_history(current)
    _append_history(target)
    _cleanup_releases(keep={target, _sha_for_release(previous) if previous else None})
    backup.unlink(missing_ok=True)
    return _result("success", request_id, "update", current, target, "تست‌ها پاس شدند و release جدید فعال شد.")


def _rollback(request_id: str) -> dict:
    _verify_control_repo()
    env = _load_env()
    _validate_runtime_env(env)
    current = _current_sha()
    history = _history()
    candidates = [sha for sha in reversed(history) if sha != current and _healthy_release_dir(RELEASES / sha, sha)]
    if not candidates:
        raise UpdateFailure("no previous healthy release is available")
    target = candidates[0]
    release = RELEASES / target
    db_path = Path(env["DRJAVAN_DATA_DIR"]) / "archive.sqlite3"
    backup = UPDATE_DIR / f"archive-before-rollback-{int(time.time())}.sqlite3"
    db_existed_before = db_path.exists()

    _run(["systemctl", "stop", SERVICE], timeout=180)
    previous = _active_release()
    try:
        _backup_sqlite(db_path, backup)
        _switch_current(release)
        prod_env = dict(os.environ)
        prod_env.update(env)
        prod_env["DRJAVAN_ARCHIVE_DIR"] = str(CURRENT / "گروه دکتر جوان")
        _run([str(CURRENT / ".venv/bin/drjavanbot"), "reindex"], cwd=CURRENT, env=prod_env, timeout=900)
        _run(["systemctl", "start", SERVICE], timeout=180)
        _wait_active(stable_seconds=4)
        _run([str(CURRENT / ".venv/bin/drjavanbot-smoke")], cwd=CURRENT, env=prod_env, timeout=120)
        _wait_active(stable_seconds=2)
    except Exception:
        _run(["systemctl", "stop", SERVICE], timeout=180, check=False)
        if previous is not None and previous.exists():
            _switch_current(previous)
        _restore_sqlite(backup, db_path, existed_before=db_existed_before)
        _run(["systemctl", "start", SERVICE], timeout=180, check=False)
        raise

    _append_history(target)
    backup.unlink(missing_ok=True)
    return _result("rolled_back", request_id, "rollback", current, target, "آخرین release سالم قبلی فعال شد.")


def _run_stage_gates(release: Path, stage_root: Path, env: dict[str, str]) -> None:
    stage_data = stage_root / "data"
    stage_cache = stage_root / "cache"
    stage_tmp = stage_root / "tmp"
    stage_pytest = stage_root / "pytest"
    for path in (stage_data, stage_cache, stage_tmp):
        path.mkdir(parents=True, exist_ok=True)

    test_env = dict(os.environ)
    test_env.update(env)
    test_env.update({
        "DRJAVAN_ARCHIVE_DIR": str(release / "گروه دکتر جوان"),
        "DRJAVAN_DATA_DIR": str(stage_data),
        "DRJAVAN_CACHE_DIR": str(stage_cache),
        "TMPDIR": str(stage_tmp),
        "TEMP": str(stage_tmp),
        "TMP": str(stage_tmp),
    })

    python = release / ".venv/bin/python"
    _run([str(python), "-m", "compileall", "-q", "src", "deploy"], cwd=release, env=test_env, timeout=120)
    _run([str(python), "-m", "pytest", "-q", "--basetemp", str(stage_pytest)], cwd=release, env=test_env, timeout=600)
    _run([str(release / ".venv/bin/drjavanbot"), "reindex"], cwd=release, env=test_env, timeout=900)
    _run([str(release / ".venv/bin/drjavanbot"), "health"], cwd=release, env=test_env, timeout=120)
    _run([str(release / ".venv/bin/drjavanbot-smoke")], cwd=release, env=test_env, timeout=120)
    if env.get("TELEGRAM_BOT_TOKEN"):
        _run([str(release / ".venv/bin/drjavanbot-smoke"), "--telegram"], cwd=release, env=test_env, timeout=120)


def _prepare_release(sha: str) -> Path:
    RELEASES.mkdir(parents=True, exist_ok=True)
    release = RELEASES / sha
    if release.exists() and not _release_matches_sha(release, sha):
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "remove", "--force", str(release)], check=False, timeout=120)
        shutil.rmtree(release, ignore_errors=True)
    if not release.exists():
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "add", "--detach", str(release), sha], timeout=180)

    python_exe = shutil.which("python3.11") or shutil.which("python3")
    if not python_exe:
        raise UpdateFailure("Python 3.11+ is not available")
    version = _run_text([python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]).strip()
    try:
        major, minor = (int(x) for x in version.split(".", 1))
    except ValueError as exc:
        raise UpdateFailure("unable to verify Python version") from exc
    if (major, minor) < (3, 11):
        raise UpdateFailure("Python 3.11+ is required")

    venv = release / ".venv"
    if not (venv / "bin/python").exists():
        shutil.rmtree(venv, ignore_errors=True)
        _run([python_exe, "-m", "venv", str(venv)], timeout=180)
    pip_python = venv / "bin/python"
    # Reconcile dependencies even for a reused candidate release. This repairs interrupted installs.
    _run([str(pip_python), "-m", "pip", "install", "-r", "requirements.lock"], cwd=release, timeout=600)
    _run([str(pip_python), "-m", "pip", "install", "-r", "requirements-dev.lock"], cwd=release, timeout=600)
    _run([str(pip_python), "-m", "pip", "install", "--no-deps", "."], cwd=release, timeout=300)
    (release / ".deploy_commit").write_text(sha + "\n", encoding="utf-8")
    os.chmod(release / ".deploy_commit", 0o644)
    return release


def _release_matches_sha(release: Path, sha: str) -> bool:
    try:
        actual = _run_text(["git", "-C", str(release), "rev-parse", "HEAD"]).strip()
    except Exception:
        return False
    return actual == sha


def _healthy_release_dir(release: Path, sha: str) -> bool:
    if not release.is_dir() or not (release / ".venv/bin/python").exists():
        return False
    marker = release / ".deploy_commit"
    try:
        return marker.read_text(encoding="utf-8").strip() == sha and _release_matches_sha(release, sha)
    except OSError:
        return False


def _verify_control_repo() -> None:
    if not (CONTROL_REPO / ".git").exists():
        raise UpdateFailure("control repository is missing")
    remote = _run_text(["git", "-C", str(CONTROL_REPO), "remote", "get-url", "origin"]).strip()
    if remote not in ALLOWED_REMOTES:
        raise UpdateFailure(f"unexpected origin remote for {EXPECTED_REPO}")
    dirty = _run_text(["git", "-C", str(CONTROL_REPO), "status", "--porcelain", "--untracked-files=no"]).strip()
    if dirty:
        raise UpdateFailure("control repository has tracked local modifications")


def _read_request() -> dict | None:
    try:
        value = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateFailure("invalid update request file") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise UpdateFailure("unsupported update request schema")
    action = value.get("action")
    request_id = value.get("request_id")
    if action not in {"update", "rollback"} or not isinstance(request_id, str) or not request_id:
        raise UpdateFailure("invalid update request")
    return {"action": action, "request_id": request_id[:64]}


def _load_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise UpdateFailure("runtime env file is missing")
    env: dict[str, str] = {}
    for number, raw in enumerate(ENV_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise UpdateFailure(f"invalid env line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not (key[0].isalpha() or key[0] == "_") or not all(ch.isalnum() or ch == "_" for ch in key):
            raise UpdateFailure(f"invalid env key on line {number}")
        if value.startswith(('"', "'")):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise UpdateFailure(f"unterminated quoted env value on line {number}")
            value = value[1:-1]
        elif any(ch.isspace() for ch in value):
            raise UpdateFailure(f"unquoted whitespace in env value on line {number}")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise UpdateFailure(f"invalid control character in env value on line {number}")
        env[key] = value
    return env


def _validate_runtime_env(env: dict[str, str]) -> None:
    required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "DRJAVAN_DATA_DIR", "DRJAVAN_CACHE_DIR")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise UpdateFailure("missing required runtime env keys: " + ",".join(missing))
    try:
        owner_id = int(env["TELEGRAM_OWNER_ID"])
    except ValueError as exc:
        raise UpdateFailure("TELEGRAM_OWNER_ID must be numeric") from exc
    if owner_id <= 0:
        raise UpdateFailure("TELEGRAM_OWNER_ID must be positive")


def _current_sha() -> str | None:
    release = _active_release()
    if release is not None:
        marker = release / ".deploy_commit"
        if marker.is_file():
            value = marker.read_text(encoding="utf-8").strip()
            if len(value) == 40:
                return value
    try:
        return _run_text(["git", "-C", str(CONTROL_REPO), "rev-parse", "HEAD"]).strip()
    except Exception:
        return None


def _active_release() -> Path | None:
    if not CURRENT.is_symlink():
        return None
    try:
        return CURRENT.resolve(strict=True)
    except OSError:
        return None


def _sha_for_release(path: Path | None) -> str | None:
    if path is None:
        return None
    marker = path / ".deploy_commit"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if len(value) == 40 else None


def _switch_current(release: Path) -> None:
    temp_link = CURRENT.parent / ".current.new"
    temp_link.unlink(missing_ok=True)
    os.symlink(release, temp_link)
    os.replace(temp_link, CURRENT)
    _fsync_directory(CURRENT.parent)


def _backup_sqlite(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    if not src.exists():
        return
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    os.chmod(dst, 0o600)


def _restore_sqlite(backup: Path, dst: Path, *, existed_before: bool) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(dst) + suffix).unlink(missing_ok=True)
    if not existed_before:
        return
    if not backup.exists():
        raise UpdateFailure("SQLite backup is missing during rollback")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, dst)


def _wait_active(*, stable_seconds: float = 4.0) -> None:
    deadline = time.monotonic() + 30
    stable_since: float | None = None
    while time.monotonic() < deadline:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", SERVICE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        now = time.monotonic()
        if active:
            stable_since = stable_since or now
            if now - stable_since >= stable_seconds:
                return
        else:
            stable_since = None
        time.sleep(0.5)
    raise UpdateFailure("drjavanbot.service did not remain active")


def _history() -> list[str]:
    try:
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, str) and len(x) == 40]


def _append_history(sha: str) -> None:
    values = [x for x in _history() if x != sha]
    values.append(sha)
    _atomic_json(HISTORY_FILE, values[-10:])


def _cleanup_releases(*, keep: set[str | None]) -> None:
    protected = {x for x in keep if x}
    history = _history()
    protected.update(history[-MAX_RELEASES:])
    active = _current_sha()
    if active:
        protected.add(active)
    if not RELEASES.exists():
        return
    for path in RELEASES.iterdir():
        if not path.is_dir() or path.name in protected:
            continue
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "remove", "--force", str(path)], check=False, timeout=120)
        shutil.rmtree(path, ignore_errors=True)


def _result(state: str, request_id: str, action: str, current_sha: str | None, target_sha: str | None, message: str) -> dict:
    return {
        "schema": 1,
        "state": state,
        "request_id": request_id,
        "action": action,
        "current_sha": current_sha,
        "target_sha": target_sha,
        "message": message[:500],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _redacted_detail(result: subprocess.CompletedProcess, env: dict[str, str] | None) -> str:
    detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()[-1200:]
    if env:
        for key, value in env.items():
            upper = key.upper()
            if value and any(marker in upper for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
                detail = detail.replace(value, "<redacted>")
    return detail


def _run_text(cmd: list[str], **kwargs) -> str:
    result = _run(cmd, **kwargs)
    return result.stdout or ""


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = _redacted_detail(result, env)
        command = " ".join(cmd[:3])
        raise UpdateFailure(f"command failed ({result.returncode}): {command}; {detail}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
