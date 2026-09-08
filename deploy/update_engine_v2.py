#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONTROL_REPO = Path("/opt/drjavanbot/app")
RELEASES = Path("/opt/drjavanbot/releases")
CURRENT = Path("/opt/drjavanbot/current")
ENV_FILE = Path("/etc/drjavanbot/drjavanbot.env")
STATE_ROOT = Path("/var/lib/drjavanbot")
UPDATE_DIR = STATE_ROOT / "update"
REQUEST_FILE = UPDATE_DIR / "request.json"
RESULT_FILE = UPDATE_DIR / "result.json"
HISTORY_FILE = UPDATE_DIR / "history.json"
HISTORY_EVENTS_FILE = UPDATE_DIR / "history-events.json"
LOCK_FILE = Path("/run/lock/drjavanbot-updater.lock")
PIP_CACHE_DIR = Path("/var/cache/drjavanbot/pip")
PIP_NETWORK_TIMEOUT_SECONDS = 60
PIP_RETRIES = 5
PIP_OUTER_ATTEMPTS = 2
PIP_PROCESS_TIMEOUT_SECONDS = 420
SERVICE = "drjavanbot.service"
SERVICE_USER = "drjavanbot"
EXPECTED_REPO = "ArianGhsm/DrJavanGroupDatabase"
ALLOWED_REMOTES = {
    "https://github.com/ArianGhsm/DrJavanGroupDatabase.git",
    "https://github.com/ArianGhsm/DrJavanGroupDatabase",
}
GITHUB_RUNS_API = "https://api.github.com/repos/ArianGhsm/DrJavanGroupDatabase/actions/runs"
EXPECTED_WORKFLOW = "DrJavanBot tests"
MAX_RELEASES = 3
PROGRESS_TOTAL = 7

_INDEX_PREFIXES = (
    "src/drjavanbot/ingest/", "src/drjavanbot/search/", "src/drjavanbot/storage/",
    "src/drjavanbot/normalization.py", "src/drjavanbot/domain/",
)
_DEPENDENCY_FILES = {"requirements.lock", "requirements-dev.lock", "pyproject.toml"}
_ARCHIVE_PREFIX = "گروه دکتر جوان/"


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
        started_at = _now()
        started_mono = time.monotonic()
        _emit_event("update_started" if action == "update" else "rollback_started", request_id=request_id)
        try:
            if action == "update":
                result = _update(request, started_at=started_at, started_mono=started_mono)
            else:
                result = _rollback(request, started_at=started_at, started_mono=started_mono)
        except Exception as exc:
            error_id = "UPD-" + secrets.token_hex(3).upper()
            result = _result(
                state="failed", request_id=request_id, action=action,
                current_sha=_current_sha(), target_sha=_sha(request.get("target_sha")),
                message="به‌روزرسانی انجام نشد؛ نسخه فعال تا حد ممکن حفظ شد.",
                stage="failed", stage_label="عملیات ناموفق",
                progress_current=0, progress_total=PROGRESS_TOTAL,
                detail=f"{type(exc).__name__}: {str(exc)[:220]}",
                started_at=started_at, error_id=error_id,
                duration_seconds=time.monotonic() - started_mono,
                ci_status=_text(request.get("ci_status")),
            )
            _emit_event("update_failed" if action == "update" else "rollback_failed", request_id=request_id, error_id=error_id, error_class=type(exc).__name__)
            print(f"drjavanbot updater failed [{error_id}]: {type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            REQUEST_FILE.unlink(missing_ok=True)
        _atomic_json(RESULT_FILE, result)
        _append_history_event(result)
        return 0 if result["state"] in {"success", "up_to_date", "rolled_back"} else 1


def _update(request: dict, *, started_at: str, started_mono: float) -> dict:
    request_id = request["request_id"]
    _progress(request_id, "update", "checking", "بررسی نسخه", 1, "هویت repository و وضعیت runtime در حال بررسی است.", started_at=started_at, current_sha=_current_sha())
    _verify_control_repo()
    env = _load_env(); _validate_runtime_env(env)
    current = _current_sha()
    _run(["git", "-C", str(CONTROL_REPO), "fetch", "--prune", "origin", "main"], timeout=180)
    origin_target = _run_text(["git", "-C", str(CONTROL_REPO), "rev-parse", "origin/main"]).strip()
    requested_target = _sha(request.get("target_sha"))
    target = requested_target or origin_target
    if not _valid_sha(target): raise UpdateFailure("unable to resolve target sha")
    if target != origin_target: raise UpdateFailure("requested target is not current origin/main")
    if current == target:
        return _result("up_to_date", request_id, "update", current, target, "ربات همین حالا به‌روز است.", stage="done", stage_label="به‌روز", progress_current=PROGRESS_TOTAL, progress_total=PROGRESS_TOTAL, started_at=started_at, duration_seconds=time.monotonic()-started_mono, ci_status="success")
    if current:
        ancestry = subprocess.run(["git", "-C", str(CONTROL_REPO), "merge-base", "--is-ancestor", current, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ancestry.returncode != 0: raise UpdateFailure("origin/main is not a fast-forward descendant of active release")

    _progress(request_id, "update", "validating", "بررسی تست‌های GitHub", 2, "exact SHA باید CI سبز داشته باشد.", started_at=started_at, current_sha=current, target_sha=target, ci_status="pending")
    ci_status = _github_ci_status(target)
    if ci_status != "success": raise UpdateFailure(f"exact target CI is not green: {ci_status}")

    changed = _changed_paths(current, target)
    change_class = _classify_changes(changed, current_sha=current)
    _progress(request_id, "update", "preparing", "آماده‌سازی نسخه", 3, _prepare_detail(change_class), started_at=started_at, current_sha=current, target_sha=target, ci_status=ci_status, change_class=change_class)
    release = _prepare_release(target, current_sha=current, change_class=change_class, request_id=request_id, started_at=started_at, ci_status=ci_status)
    stage_root = UPDATE_DIR / f"stage-{target[:12]}"
    shutil.rmtree(stage_root, ignore_errors=True)
    previous = _active_release()
    db_path = Path(env["DRJAVAN_DATA_DIR"]) / "archive.sqlite3"
    backup = UPDATE_DIR / f"archive-before-{target[:12]}.sqlite3"
    db_existed_before = db_path.exists()

    try:
        _progress(request_id, "update", "validating", "اعتبارسنجی production", 4, "فقط gateهای production لازم اجرا می‌شوند؛ pytest و Quality Lab به CI واگذار شده‌اند.", started_at=started_at, current_sha=current, target_sha=target, ci_status=ci_status, change_class=change_class)
        stage_db = _run_stage_gates(release, stage_root, env, change_class=change_class, request_id=request_id, current_sha=current, target_sha=target, started_at=started_at, ci_status=ci_status)
        _make_release_runtime_readable(release)
        _progress(request_id, "update", "switching", "فعال‌سازی نسخه", 5, "candidate آماده است؛ توقف سرویس فقط برای atomic switch انجام می‌شود.", started_at=started_at, current_sha=current, target_sha=target, ci_status=ci_status, change_class=change_class)
        _run(["systemctl", "stop", SERVICE], timeout=180)
        try:
            if stage_db is not None:
                _backup_sqlite(db_path, backup)
            _switch_current(release)
            if stage_db is not None:
                _promote_sqlite(stage_db, db_path)
            prod_env = dict(os.environ); prod_env.update(env); prod_env["DRJAVAN_ARCHIVE_DIR"] = str(CURRENT / "گروه دکتر جوان")
            _progress(request_id, "update", "verifying", "بررسی سلامت و تلگرام", 6, "سرویس جدید راه‌اندازی شده و smoke نهایی در حال اجراست.", started_at=started_at, current_sha=current, target_sha=target, ci_status=ci_status, change_class=change_class)
            _run(["systemctl", "start", SERVICE], timeout=180)
            _wait_active(stable_seconds=3)
            _run([str(CURRENT / ".venv/bin/drjavanbot-smoke")], cwd=CURRENT, env=prod_env, timeout=120)
            if env.get("TELEGRAM_BOT_TOKEN"):
                _run([str(CURRENT / ".venv/bin/drjavanbot-smoke"), "--telegram"], cwd=CURRENT, env=prod_env, timeout=120)
            _wait_active(stable_seconds=2)
        except Exception:
            _run(["systemctl", "stop", SERVICE], timeout=180, check=False)
            if previous is not None and previous.exists():
                _make_release_runtime_readable(previous); _switch_current(previous)
            if stage_db is not None:
                _restore_sqlite(backup, db_path, existed_before=db_existed_before)
            _run(["systemctl", "start", SERVICE], timeout=180, check=False)
            raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    if current: _append_history(current)
    _append_history(target)
    _cleanup_releases(keep={target, _sha_for_release(previous) if previous else None})
    backup.unlink(missing_ok=True)
    duration = time.monotonic() - started_mono
    _emit_event("update_succeeded", request_id=request_id, target_sha=target[:12], change_class=change_class)
    return _result("success", request_id, "update", current, target, "نسخه جدید فعال و smoke نهایی موفق شد.", stage="done", stage_label="پایان", progress_current=PROGRESS_TOTAL, progress_total=PROGRESS_TOTAL, started_at=started_at, duration_seconds=duration, ci_status=ci_status, change_class=change_class)


def _rollback(request: dict, *, started_at: str, started_mono: float) -> dict:
    request_id = request["request_id"]
    _verify_control_repo(); env = _load_env(); _validate_runtime_env(env)
    current = _current_sha(); history = _history()
    candidates = [sha for sha in reversed(history) if sha != current and _healthy_release_dir(RELEASES / sha, sha)]
    if not candidates: raise UpdateFailure("no previous healthy release is available")
    target = candidates[0]; release = RELEASES / target
    _progress(request_id, "rollback", "preparing", "آماده‌سازی بازگشت", 3, "نسخه قبلی و ایندکس سازگار در کنار نسخه فعال آماده می‌شوند.", started_at=started_at, current_sha=current, target_sha=target, change_class="rollback")
    stage_root = UPDATE_DIR / f"rollback-stage-{target[:12]}"; shutil.rmtree(stage_root, ignore_errors=True)
    stage_db = _run_stage_gates(release, stage_root, env, change_class="archive", request_id=request_id, current_sha=current, target_sha=target, started_at=started_at, ci_status="previously_healthy")
    if stage_db is None: raise UpdateFailure("rollback staging database was not produced")
    _make_release_runtime_readable(release)
    db_path = Path(env["DRJAVAN_DATA_DIR"]) / "archive.sqlite3"; backup = UPDATE_DIR / f"archive-before-rollback-{int(time.time())}.sqlite3"; db_existed_before = db_path.exists(); previous = _active_release()
    _progress(request_id, "rollback", "switching", "فعال‌سازی نسخه قبلی", 5, "atomic switch در حال انجام است.", started_at=started_at, current_sha=current, target_sha=target, change_class="rollback")
    _run(["systemctl", "stop", SERVICE], timeout=180)
    try:
        _backup_sqlite(db_path, backup); _switch_current(release); _promote_sqlite(stage_db, db_path)
        prod_env = dict(os.environ); prod_env.update(env); prod_env["DRJAVAN_ARCHIVE_DIR"] = str(CURRENT / "گروه دکتر جوان")
        _progress(request_id, "rollback", "verifying", "بررسی سلامت و تلگرام", 6, "نسخه قبلی فعال شده و smoke نهایی در حال اجراست.", started_at=started_at, current_sha=current, target_sha=target, change_class="rollback")
        _run(["systemctl", "start", SERVICE], timeout=180); _wait_active(stable_seconds=3)
        _run([str(CURRENT / ".venv/bin/drjavanbot-smoke")], cwd=CURRENT, env=prod_env, timeout=120)
        if env.get("TELEGRAM_BOT_TOKEN"): _run([str(CURRENT / ".venv/bin/drjavanbot-smoke"), "--telegram"], cwd=CURRENT, env=prod_env, timeout=120)
    except Exception:
        _run(["systemctl", "stop", SERVICE], timeout=180, check=False)
        if previous is not None and previous.exists(): _make_release_runtime_readable(previous); _switch_current(previous)
        _restore_sqlite(backup, db_path, existed_before=db_existed_before); _run(["systemctl", "start", SERVICE], timeout=180, check=False); raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    _append_history(target); backup.unlink(missing_ok=True)
    duration = time.monotonic() - started_mono
    _emit_event("rollback_succeeded", request_id=request_id, target_sha=target[:12])
    return _result("rolled_back", request_id, "rollback", current, target, "نسخه قبلی سالم فعال شد.", stage="done", stage_label="پایان", progress_current=PROGRESS_TOTAL, progress_total=PROGRESS_TOTAL, started_at=started_at, duration_seconds=duration, change_class="rollback")


def _changed_paths(current_sha: str | None, target_sha: str) -> tuple[str, ...]:
    if not current_sha: return ("__initial_release__",)
    text = _run_text(["git", "-C", str(CONTROL_REPO), "diff", "--name-only", current_sha, target_sha], timeout=120)
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _classify_changes(paths: tuple[str, ...], *, current_sha: str | None) -> str:
    if not current_sha or "__initial_release__" in paths: return "mixed"
    dependency = any(path in _DEPENDENCY_FILES for path in paths)
    archive = any(path.startswith(_ARCHIVE_PREFIX) for path in paths)
    index = any(any(path.startswith(prefix) for prefix in _INDEX_PREFIXES) for path in paths)
    classes = sum((dependency, archive, index))
    if classes > 1: return "mixed"
    if archive: return "archive"
    if index: return "index"
    if dependency: return "dependency"
    return "code_only"


def _prepare_detail(change_class: str) -> str:
    return {
        "code_only": "انتشار فقط کد است؛ محیط Python نسخه سالم قبلی reuse می‌شود و reindex اجرا نمی‌شود.",
        "dependency": "وابستگی‌ها تغییر کرده‌اند؛ محیط Python تازه از lockها ساخته می‌شود.",
        "index": "کد ایندکس تغییر کرده؛ candidate DB قبل از switch ساخته می‌شود.",
        "archive": "آرشیو تغییر کرده؛ candidate DB کامل قبل از switch ساخته می‌شود.",
        "mixed": "تغییرات ترکیبی است؛ مسیر محافظه‌کارانه کامل اجرا می‌شود.",
    }.get(change_class, "candidate در حال آماده‌سازی است.")


def _prepare_release(sha: str, *, current_sha: str | None, change_class: str, request_id: str | None = None, started_at: str | None = None, ci_status: str | None = None) -> Path:
    RELEASES.mkdir(parents=True, exist_ok=True); os.chmod(RELEASES, 0o755)
    try: os.chmod(CURRENT.parent, 0o755)
    except OSError: pass
    release = RELEASES / sha
    if release.exists() and not _release_matches_sha(release, sha):
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "remove", "--force", str(release)], check=False, timeout=120); shutil.rmtree(release, ignore_errors=True)
    if not release.exists(): _run(["git", "-C", str(CONTROL_REPO), "worktree", "add", "--detach", str(release), sha], timeout=180)

    python_exe = shutil.which("python3.11") or shutil.which("python3")
    if not python_exe: raise UpdateFailure("Python 3.11+ is not available")
    version = _run_text([python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]).strip()
    try: major, minor = (int(x) for x in version.split(".", 1))
    except ValueError as exc: raise UpdateFailure("unable to verify Python version") from exc
    if (major, minor) < (3, 11): raise UpdateFailure("Python 3.11+ is required")

    venv = release / ".venv"
    active = _active_release(); active_venv = active / ".venv" if active else None
    reuse = change_class in {"code_only", "index", "archive"} and active_venv is not None and (active_venv / "bin/python").exists()
    if not (venv / "bin/python").exists():
        shutil.rmtree(venv, ignore_errors=True)
        if reuse:
            try:
                shutil.copytree(active_venv, venv, symlinks=True)
            except OSError:
                shutil.rmtree(venv, ignore_errors=True); reuse = False
        if not reuse:
            _run([python_exe, "-m", "venv", "--system-site-packages", str(venv)], timeout=180)
            _pip_install(venv / "bin/python", ["-r", "requirements-dev.lock"], cwd=release, request_id=request_id, current_sha=current_sha, target_sha=sha)
    pip_python = venv / "bin/python"
    build_env = dict(os.environ); build_env["PYTHONPATH"] = "/usr/lib/python3/dist-packages" + ((":" + build_env["PYTHONPATH"]) if build_env.get("PYTHONPATH") else "")
    _run([str(pip_python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", "."], cwd=release, env=build_env, timeout=300)
    _run([str(pip_python), "-m", "compileall", "-q", "src", "deploy"], cwd=release, timeout=120)
    (release / ".deploy_commit").write_text(sha + "\n", encoding="utf-8"); os.chmod(release / ".deploy_commit", 0o644)
    return release


def _run_stage_gates(release: Path, stage_root: Path, env: dict[str, str], *, change_class: str = "index", request_id: str | None = None, current_sha: str | None = None, target_sha: str | None = None, started_at: str | None = None, ci_status: str | None = None) -> Path | None:
    stage_data = stage_root / "data"; stage_cache = stage_root / "cache"; stage_tmp = stage_root / "tmp"
    for path in (stage_data, stage_cache, stage_tmp): path.mkdir(parents=True, exist_ok=True)
    test_env = dict(os.environ); test_env.update(env); test_env.update({"DRJAVAN_ARCHIVE_DIR": str(release / "گروه دکتر جوان"), "TMPDIR": str(stage_tmp), "TEMP": str(stage_tmp), "TMP": str(stage_tmp)})
    needs_index = change_class in {"index", "archive", "mixed", "rollback"}
    if needs_index:
        test_env["DRJAVAN_DATA_DIR"] = str(stage_data); test_env["DRJAVAN_CACHE_DIR"] = str(stage_cache)
        _run([str(release / ".venv/bin/drjavanbot"), "reindex"], cwd=release, env=test_env, timeout=900)
        stage_db = stage_data / "archive.sqlite3"
        if not stage_db.is_file(): raise UpdateFailure("staging reindex did not produce archive.sqlite3")
    else:
        # Code/dependency-only releases validate against the existing production
        # database and never rebuild it just because code changed.
        test_env["DRJAVAN_DATA_DIR"] = env["DRJAVAN_DATA_DIR"]; test_env["DRJAVAN_CACHE_DIR"] = env["DRJAVAN_CACHE_DIR"]
        stage_db = None
    _run([str(release / ".venv/bin/drjavanbot"), "health"], cwd=release, env=test_env, timeout=120)
    _run([str(release / ".venv/bin/drjavanbot-smoke")], cwd=release, env=test_env, timeout=120)
    if env.get("TELEGRAM_BOT_TOKEN"): _run([str(release / ".venv/bin/drjavanbot-smoke"), "--telegram"], cwd=release, env=test_env, timeout=120)
    return stage_db


def _github_ci_status(sha: str, *, timeout: float = 12.0) -> str:
    query = urlencode({"head_sha": sha, "event": "push", "per_page": 20})
    request = Request(f"{GITHUB_RUNS_API}?{query}", headers={"Accept":"application/vnd.github+json","User-Agent":"DrJavanBot-root-updater/2","X-GitHub-Api-Version":"2022-11-28"}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            if int(getattr(response, "status", 200)) != 200: return "unknown"
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError): return "unknown"
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list): return "unknown"
    matching = [run for run in runs if isinstance(run, dict) and str(run.get("head_sha") or "") == sha and str(run.get("name") or "") == EXPECTED_WORKFLOW]
    if not matching: return "unknown"
    matching.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True); run = matching[0]
    status = str(run.get("status") or "").casefold(); conclusion = str(run.get("conclusion") or "").casefold()
    if status in {"queued","in_progress","waiting","pending","requested"}: return "pending"
    if status == "completed" and conclusion == "success": return "success"
    if status == "completed": return "failure"
    return "unknown"


def _pip_install(pip_python: Path, args: list[str], *, cwd: Path, request_id: str | None = None, current_sha: str | None = None, target_sha: str | None = None) -> None:
    PIP_CACHE_DIR.mkdir(parents=True, exist_ok=True); os.chmod(PIP_CACHE_DIR, 0o700)
    pip_env = dict(os.environ); pip_env.update({"PIP_CACHE_DIR":str(PIP_CACHE_DIR),"PIP_DEFAULT_TIMEOUT":str(PIP_NETWORK_TIMEOUT_SECONDS),"PIP_RETRIES":str(PIP_RETRIES),"PIP_DISABLE_PIP_VERSION_CHECK":"1","PIP_NO_INPUT":"1"})
    command = [str(pip_python),"-m","pip","install","--disable-pip-version-check","--no-input","--prefer-binary","--timeout",str(PIP_NETWORK_TIMEOUT_SECONDS),"--retries",str(PIP_RETRIES),*args]
    last_error: Exception | None = None
    for attempt in range(1, PIP_OUTER_ATTEMPTS + 1):
        try:
            _run(command,cwd=cwd,env=pip_env,timeout=PIP_PROCESS_TIMEOUT_SECONDS); return
        except (UpdateFailure, subprocess.TimeoutExpired) as exc:
            last_error=exc
            if attempt>=PIP_OUTER_ATTEMPTS: break
            if request_id: _progress(request_id,"update","preparing","آماده‌سازی نسخه",3,f"دریافت وابستگی‌ها قطع شد؛ تلاش {attempt+1}/{PIP_OUTER_ATTEMPTS}.",current_sha=current_sha,target_sha=target_sha)
            time.sleep(2.0*attempt)
    if isinstance(last_error,UpdateFailure): raise last_error
    if isinstance(last_error,subprocess.TimeoutExpired): raise UpdateFailure("pip dependency installation exceeded bounded timeout") from last_error
    raise UpdateFailure("pip dependency installation failed")


def _make_release_runtime_readable(release: Path) -> None:
    if not release.is_dir(): raise UpdateFailure("release directory is missing before activation")
    try: os.chmod(CURRENT.parent,0o755); os.chmod(RELEASES,0o755)
    except OSError as exc: raise UpdateFailure("unable to make release parents traversable") from exc
    for root,dirs,files in os.walk(release,followlinks=False):
        root_path=Path(root); os.chmod(root_path,0o755)
        try: os.chown(root_path,0,0)
        except PermissionError as exc: raise UpdateFailure("updater must run as root to publish releases") from exc
        for name in dirs:
            path=root_path/name
            if path.is_symlink():
                try: os.lchown(path,0,0)
                except OSError: pass
        for name in files:
            path=root_path/name
            if path.is_symlink():
                try: os.lchown(path,0,0)
                except OSError: pass
                continue
            mode=path.stat().st_mode; os.chmod(path,0o755 if mode & 0o111 else 0o644); os.chown(path,0,0)
    bot=release/".venv/bin/drjavanbot-bot"
    if not bot.is_file() or not os.access(bot,os.X_OK): raise UpdateFailure("release entrypoint is not executable after permission hardening")


def _release_matches_sha(release: Path, sha: str) -> bool:
    try: actual=_run_text(["git","-C",str(release),"rev-parse","HEAD"]).strip()
    except Exception: return False
    return actual==sha

def _healthy_release_dir(release: Path, sha: str) -> bool:
    if not release.is_dir() or not (release/".venv/bin/python").exists(): return False
    try: return (release/".deploy_commit").read_text(encoding="utf-8").strip()==sha and _release_matches_sha(release,sha)
    except OSError: return False

def _verify_control_repo() -> None:
    if not (CONTROL_REPO/".git").exists(): raise UpdateFailure("control repository is missing")
    remote=_run_text(["git","-C",str(CONTROL_REPO),"remote","get-url","origin"]).strip()
    if remote not in ALLOWED_REMOTES: raise UpdateFailure(f"unexpected origin remote for {EXPECTED_REPO}")
    dirty=_run_text(["git","-C",str(CONTROL_REPO),"status","--porcelain","--untracked-files=no"]).strip()
    if dirty: raise UpdateFailure("control repository has tracked local modifications")

def _read_request() -> dict | None:
    try: value=json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError: return None
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc: raise UpdateFailure("invalid update request file") from exc
    if not isinstance(value,dict) or value.get("schema")!=1: raise UpdateFailure("unsupported update request schema")
    action=value.get("action"); request_id=value.get("request_id")
    if action not in {"update","rollback"} or not isinstance(request_id,str) or not request_id: raise UpdateFailure("invalid update request")
    target=_sha(value.get("target_sha"))
    return {"action":action,"request_id":request_id[:64],"target_sha":target,"ci_status":_text(value.get("ci_status")),"source":_text(value.get("source")) or "manual"}

def _load_env() -> dict[str,str]:
    if not ENV_FILE.is_file(): raise UpdateFailure("runtime env file is missing")
    env={}
    for number,raw in enumerate(ENV_FILE.read_text(encoding="utf-8").splitlines(),start=1):
        line=raw.strip()
        if not line or line.startswith("#"): continue
        if "=" not in line: raise UpdateFailure(f"invalid env line {number}")
        key,value=line.split("=",1); key=key.strip(); value=value.strip()
        if not key or not (key[0].isalpha() or key[0]=="_") or not all(ch.isalnum() or ch=="_" for ch in key): raise UpdateFailure(f"invalid env key on line {number}")
        if value.startswith(('"',"'")):
            quote=value[0]
            if len(value)<2 or value[-1]!=quote: raise UpdateFailure(f"unterminated quoted env value on line {number}")
            value=value[1:-1]
        elif any(ch.isspace() for ch in value): raise UpdateFailure(f"unquoted whitespace in env value on line {number}")
        if "\x00" in value or "\n" in value or "\r" in value: raise UpdateFailure(f"invalid control character in env value on line {number}")
        env[key]=value
    return env

def _validate_runtime_env(env: dict[str,str]) -> None:
    required=("TELEGRAM_BOT_TOKEN","TELEGRAM_OWNER_ID","DRJAVAN_DATA_DIR","DRJAVAN_CACHE_DIR"); missing=[key for key in required if not env.get(key)]
    if missing: raise UpdateFailure("missing required runtime env keys: "+",".join(missing))
    try: owner_id=int(env["TELEGRAM_OWNER_ID"])
    except ValueError as exc: raise UpdateFailure("TELEGRAM_OWNER_ID must be numeric") from exc
    if owner_id<=0: raise UpdateFailure("TELEGRAM_OWNER_ID must be positive")

def _current_sha() -> str | None:
    release=_active_release()
    if release is not None:
        try:
            value=(release/".deploy_commit").read_text(encoding="utf-8").strip()
            if _valid_sha(value): return value
        except OSError: pass
    try:
        value=_run_text(["git","-C",str(CONTROL_REPO),"rev-parse","HEAD"]).strip(); return value if _valid_sha(value) else None
    except Exception: return None

def _active_release() -> Path | None:
    if not CURRENT.is_symlink(): return None
    try: return CURRENT.resolve(strict=True)
    except OSError: return None

def _sha_for_release(path: Path | None) -> str | None:
    if path is None: return None
    try: value=(path/".deploy_commit").read_text(encoding="utf-8").strip()
    except OSError: return None
    return value if _valid_sha(value) else None

def _switch_current(release: Path) -> None:
    temp=CURRENT.parent/".current.new"; temp.unlink(missing_ok=True); os.symlink(release,temp); os.replace(temp,CURRENT); _fsync_directory(CURRENT.parent)

def _service_ids() -> tuple[int,int]:
    try: account=pwd.getpwnam(SERVICE_USER)
    except KeyError as exc: raise UpdateFailure("drjavanbot Unix user is missing") from exc
    return account.pw_uid,account.pw_gid

def _make_database_service_owned(path: Path) -> None:
    if not path.exists(): return
    uid,gid=_service_ids(); os.chown(path,uid,gid); os.chmod(path,0o600)
    for suffix in ("-wal","-shm","-journal"):
        sidecar=Path(str(path)+suffix)
        if sidecar.exists(): os.chown(sidecar,uid,gid); os.chmod(sidecar,0o600)

def _backup_sqlite(src: Path,dst: Path) -> None:
    dst.unlink(missing_ok=True)
    if not src.exists(): return
    source=sqlite3.connect(src); target=sqlite3.connect(dst)
    try: source.backup(target)
    finally: target.close(); source.close()
    os.chmod(dst,0o600)

def _promote_sqlite(src: Path,dst: Path) -> None:
    if not src.is_file(): raise UpdateFailure("staging archive database is missing")
    dst.parent.mkdir(parents=True,exist_ok=True); fd,temp_name=tempfile.mkstemp(prefix=".archive.promote.",suffix=".sqlite3",dir=dst.parent); os.close(fd); temp=Path(temp_name)
    try:
        source=sqlite3.connect(f"file:{src}?mode=ro",uri=True); target=sqlite3.connect(temp)
        try: source.backup(target)
        finally: target.close(); source.close()
        for suffix in ("-wal","-shm","-journal"): Path(str(dst)+suffix).unlink(missing_ok=True)
        uid,gid=_service_ids(); os.chown(temp,uid,gid); os.chmod(temp,0o600); os.replace(temp,dst); _fsync_directory(dst.parent)
    finally: temp.unlink(missing_ok=True)

def _restore_sqlite(backup: Path,dst: Path,*,existed_before: bool) -> None:
    for suffix in ("","-wal","-shm","-journal"): Path(str(dst)+suffix).unlink(missing_ok=True)
    if not existed_before: return
    if not backup.exists(): raise UpdateFailure("SQLite backup is missing during rollback")
    dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(backup,dst); _make_database_service_owned(dst)

def _wait_active(*,stable_seconds: float=4.0) -> None:
    deadline=time.monotonic()+30; stable_since=None
    while time.monotonic()<deadline:
        active=subprocess.run(["systemctl","is-active","--quiet",SERVICE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0; now=time.monotonic()
        if active:
            stable_since=stable_since or now
            if now-stable_since>=stable_seconds: return
        else: stable_since=None
        time.sleep(0.5)
    raise UpdateFailure("drjavanbot.service did not remain active")

def _history() -> list[str]:
    try: value=json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError,OSError,UnicodeDecodeError,json.JSONDecodeError): return []
    if not isinstance(value,list): return []
    return [str(x) for x in value if isinstance(x,str) and _valid_sha(x)]

def _append_history(sha: str) -> None:
    values=[x for x in _history() if x!=sha]; values.append(sha); _atomic_json(HISTORY_FILE,values[-10:])

def _append_history_event(result: dict) -> None:
    try: value=json.loads(HISTORY_EVENTS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError,OSError,UnicodeDecodeError,json.JSONDecodeError): value=[]
    if not isinstance(value,list): value=[]
    event={key:result.get(key) for key in ("action","state","target_sha","current_sha","started_at","updated_at","duration_seconds","change_class","error_id") if result.get(key) is not None}; event["result"]=result.get("state")
    value.append(event); _atomic_json(HISTORY_EVENTS_FILE,value[-20:])

def _cleanup_releases(*,keep:set[str|None]) -> None:
    protected={x for x in keep if x}; history=_history(); protected.update(history[-MAX_RELEASES:]); active=_current_sha()
    if active: protected.add(active)
    if not RELEASES.exists(): return
    for path in RELEASES.iterdir():
        if not path.is_dir() or path.name in protected: continue
        _run(["git","-C",str(CONTROL_REPO),"worktree","remove","--force",str(path)],check=False,timeout=120); shutil.rmtree(path,ignore_errors=True)

def _progress(request_id:str,action:str,state:str,stage_label:str,progress_current:int,message:str,*,current_sha:str|None=None,target_sha:str|None=None,started_at:str|None=None,ci_status:str|None=None,change_class:str|None=None,detail:str|None=None) -> None:
    payload=_result(state,request_id,action,current_sha,target_sha,message,stage=state,stage_label=stage_label,progress_current=progress_current,progress_total=PROGRESS_TOTAL,started_at=started_at,ci_status=ci_status,change_class=change_class,detail=detail)
    _atomic_json(RESULT_FILE,payload); _emit_event("update_stage_changed",request_id=request_id,stage=state,step=progress_current)

def _result(state:str,request_id:str,action:str,current_sha:str|None,target_sha:str|None,message:str,*,stage:str|None=None,stage_label:str|None=None,progress_current:int=0,progress_total:int=0,detail:str|None=None,started_at:str|None=None,error_id:str|None=None,duration_seconds:float|None=None,ci_status:str|None=None,change_class:str|None=None) -> dict:
    return {"schema":2,"state":state,"request_id":request_id,"action":action,"current_sha":current_sha,"target_sha":target_sha,"message":message[:500],"detail":(detail or "")[:500] or None,"stage":stage,"stage_label":stage_label,"progress_current":int(progress_current),"progress_total":int(progress_total),"started_at":started_at,"updated_at":_now(),"error_id":error_id,"duration_seconds":duration_seconds,"ci_status":ci_status,"change_class":change_class}

def _atomic_json(path:Path,value) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); fd,temp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle: json.dump(value,handle,ensure_ascii=False,separators=(",",":")); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temp,0o600)
        try: uid,gid=_service_ids(); os.chown(temp,uid,gid)
        except UpdateFailure: pass
        os.replace(temp,path); _fsync_directory(path.parent)
    finally:
        try: os.unlink(temp)
        except FileNotFoundError: pass

def _fsync_directory(path:Path) -> None:
    try: fd=os.open(path,os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
    except OSError: return
    try: os.fsync(fd)
    finally: os.close(fd)

def _redacted_detail(result:subprocess.CompletedProcess,env:dict[str,str]|None) -> str:
    detail=((result.stderr or "")+"\n"+(result.stdout or "")).strip()[-1200:]
    if env:
        for key,value in env.items():
            if value and any(marker in key.upper() for marker in ("TOKEN","KEY","SECRET","PASSWORD")): detail=detail.replace(value,"<redacted>")
    return detail

def _run_text(cmd:list[str],**kwargs)->str: return _run(cmd,**kwargs).stdout or ""
def _run(cmd:list[str],*,cwd:Path|None=None,env:dict[str,str]|None=None,timeout:int=120,check:bool=True)->subprocess.CompletedProcess:
    result=subprocess.run(cmd,cwd=str(cwd) if cwd else None,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if check and result.returncode!=0:
        detail=_redacted_detail(result,env); command=" ".join(cmd[:3]); raise UpdateFailure(f"command failed ({result.returncode}): {command}; {detail}")
    return result

def _emit_event(name:str,**fields) -> None:
    safe=" ".join(f"{key}={str(value)[:80]}" for key,value in fields.items() if value is not None); print(f"{name} {safe}".strip())
def _valid_sha(value:str)->bool: return len(value)==40 and all(ch in "0123456789abcdefABCDEF" for ch in value)
def _sha(value)->str|None:
    text=str(value or "").strip(); return text if _valid_sha(text) else None
def _text(value)->str|None:
    text=str(value or "").strip(); return text[:500] if text else None
def _now()->str: return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
