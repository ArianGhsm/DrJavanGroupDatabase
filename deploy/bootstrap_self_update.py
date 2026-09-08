#!/usr/bin/env python3
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import tempfile
import time

CONTROL_REPO = Path("/opt/drjavanbot/app")
RELEASES = Path("/opt/drjavanbot/releases")
CURRENT = Path("/opt/drjavanbot/current")
ENV_FILE = Path("/etc/drjavanbot/drjavanbot.env")
UPDATE_DIR = Path("/var/lib/drjavanbot/update")
SYSTEMD = Path("/etc/systemd/system")
EXPECTED_REMOTES = {
    "https://github.com/ArianGhsm/DrJavanGroupDatabase.git",
    "https://github.com/ArianGhsm/DrJavanGroupDatabase",
}
UNIT_NAMES = ("drjavanbot.service", "drjavanbot-updater.service", "drjavanbot-updater.path")
LOCK_FILE = Path("/run/lock/drjavanbot-updater.lock")
PIP_CACHE_DIR = Path("/var/cache/drjavanbot/pip")
PIP_NETWORK_TIMEOUT_SECONDS = 60
PIP_RETRIES = 5
PIP_OUTER_ATTEMPTS = 2
PIP_PROCESS_TIMEOUT_SECONDS = 420


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("bootstrap must run as root")

    # Bootstrap and the path-triggered updater share one lock.  Without this
    # coordination a manual bootstrap can switch ``current`` while an older
    # updater process is still running; that process may then stop the newly
    # selected service after its long reindex, leaving the bot offline.
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_FILE.open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

    account, python_exe, sha = _preflight()
    _rewrite_env_archive_path()

    release = _prepare_release(sha, python_exe)
    _run_release_tests(release)
    (release / ".deploy_commit").write_text(sha + "\n", encoding="utf-8")
    os.chmod(release / ".deploy_commit", 0o644)
    _make_release_runtime_readable(release)

    previous_release = _active_release()
    previous_units = _snapshot_units()
    switched = False
    try:
        _switch_current(release)
        switched = True
        _prepare_update_state(account, sha, previous_release)
        _install_units()
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", "--now", "drjavanbot-updater.path"])
        _run(["systemctl", "restart", "drjavanbot.service"])
        _wait_service_active()
    except Exception:
        if switched and previous_release is not None and previous_release.exists():
            _make_release_runtime_readable(previous_release)
            _switch_current(previous_release)
        _restore_units(previous_units)
        _run(["systemctl", "daemon-reload"], check=False)
        _run(["systemctl", "restart", "drjavanbot.service"], check=False)
        raise

    print(f"self-updater bootstrap complete at {sha[:12]}")
    return 0


def _preflight():
    if not (CONTROL_REPO / ".git").exists():
        raise SystemExit("control repository is missing")
    remote = _text(["git", "-C", str(CONTROL_REPO), "remote", "get-url", "origin"]).strip()
    if remote not in EXPECTED_REMOTES:
        raise SystemExit("unexpected repository origin")
    dirty = _text(["git", "-C", str(CONTROL_REPO), "status", "--porcelain", "--untracked-files=no"]).strip()
    if dirty:
        raise SystemExit("control repository has tracked local modifications; refusing bootstrap")
    if not ENV_FILE.is_file():
        raise SystemExit("runtime env file is missing")
    if not (CONTROL_REPO / "گروه دکتر جوان").is_dir():
        raise SystemExit("archive directory is missing from control repository")
    for name in UNIT_NAMES:
        if not (CONTROL_REPO / "deploy" / name).is_file():
            raise SystemExit(f"missing systemd unit: {name}")
    try:
        account = pwd.getpwnam("drjavanbot")
    except KeyError as exc:
        raise SystemExit("Unix user drjavanbot is missing") from exc

    python_exe = shutil.which("python3.11") or shutil.which("python3")
    if not python_exe:
        raise SystemExit("Python 3.11+ is required")
    version = _text([python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]).strip()
    major, minor = (int(x) for x in version.split(".", 1))
    if (major, minor) < (3, 11):
        raise SystemExit("Python 3.11+ is required")
    _verify_fts5(python_exe)
    sha = _text(["git", "-C", str(CONTROL_REPO), "rev-parse", "HEAD"]).strip()
    if len(sha) != 40:
        raise SystemExit("unable to resolve repository HEAD")
    return account, python_exe, sha


def _verify_fts5(python_exe: str) -> None:
    code = (
        "import sqlite3; "
        "c=sqlite3.connect(':memory:'); "
        "c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); "
        "c.close()"
    )
    _run([python_exe, "-c", code])


def _prepare_release(sha: str, python_exe: str) -> Path:
    RELEASES.mkdir(parents=True, exist_ok=True)
    os.chmod(CURRENT.parent, 0o755)
    os.chmod(RELEASES, 0o755)
    release = RELEASES / sha
    if release.exists() and not _release_matches_sha(release, sha):
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "remove", "--force", str(release)], check=False)
        shutil.rmtree(release, ignore_errors=True)
    if not release.exists():
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "add", "--detach", str(release), sha])

    venv = release / ".venv"
    reused = _reuse_active_venv(release)
    has_setuptools = (venv / "bin/python").exists() and subprocess.run([str(venv / "bin/python"), "-c", "import setuptools"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not reused and not has_setuptools:
        shutil.rmtree(venv, ignore_errors=True)
        _run([python_exe, "-m", "venv", "--system-site-packages", str(venv)])

    vpython = str(venv / "bin/python")
    if not reused:
        _pip_install(vpython, ["-r", "requirements.lock"], cwd=release)
        _pip_install(vpython, ["-r", "requirements-dev.lock"], cwd=release)
    _run([vpython, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "."], cwd=release)
    return release


def _reuse_active_venv(release: Path) -> bool:
    active = _active_release()
    if active is None or active == release:
        return False
    active_venv = active / ".venv"
    if not (active_venv / "bin/python").exists():
        return False
    for name in ("requirements.lock", "requirements-dev.lock"):
        if not ((active / name).is_file() and (release / name).is_file()):
            return False
        if (active / name).read_bytes() != (release / name).read_bytes():
            return False
    shutil.rmtree(release / ".venv", ignore_errors=True)
    shutil.copytree(active_venv, release / ".venv", symlinks=True)
    return True


def _pip_install(python: str, args: list[str], *, cwd: Path) -> None:
    """Install dependencies with a persistent cache and bounded retries."""
    PIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(PIP_CACHE_DIR, 0o700)
    env = dict(os.environ)
    env.update({
        "PIP_CACHE_DIR": str(PIP_CACHE_DIR),
        "PIP_DEFAULT_TIMEOUT": str(PIP_NETWORK_TIMEOUT_SECONDS),
        "PIP_RETRIES": str(PIP_RETRIES),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    })
    command = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--prefer-binary", "--timeout", str(PIP_NETWORK_TIMEOUT_SECONDS), "--retries", str(PIP_RETRIES), *args]
    last: Exception | None = None
    for attempt in range(PIP_OUTER_ATTEMPTS):
        try:
            _run(command, cwd=cwd, env=env, timeout=PIP_PROCESS_TIMEOUT_SECONDS)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last = exc
            if attempt + 1 < PIP_OUTER_ATTEMPTS:
                time.sleep(2.0 * (attempt + 1))
    assert last is not None
    raise last


def _make_release_runtime_readable(release: Path) -> None:
    """Root owns releases; drjavanbot gets read/execute only."""
    if not release.is_dir():
        raise RuntimeError("release directory missing")
    os.chmod(CURRENT.parent, 0o755)
    os.chmod(RELEASES, 0o755)
    for root, dirs, files in os.walk(release, followlinks=False):
        root_path = Path(root)
        os.chmod(root_path, 0o755)
        os.chown(root_path, 0, 0)
        for name in dirs:
            path = root_path / name
            if path.is_symlink():
                try:
                    os.lchown(path, 0, 0)
                except OSError:
                    pass
        for name in files:
            path = root_path / name
            if path.is_symlink():
                try:
                    os.lchown(path, 0, 0)
                except OSError:
                    pass
                continue
            mode = path.stat().st_mode
            os.chmod(path, 0o755 if mode & 0o111 else 0o644)
            os.chown(path, 0, 0)
    entrypoint = release / ".venv/bin/drjavanbot-bot"
    if not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        raise RuntimeError("release entrypoint is not executable")


def _release_matches_sha(release: Path, sha: str) -> bool:
    try:
        actual = _text(["git", "-C", str(release), "rev-parse", "HEAD"]).strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return actual == sha


def _run_release_tests(release: Path) -> None:
    vpython = release / ".venv/bin/python"
    with tempfile.TemporaryDirectory(prefix="drjavanbot-bootstrap-", dir="/var/tmp") as temp_root:
        test_env = dict(os.environ)
        test_env.update({"TMPDIR": temp_root, "TEMP": temp_root, "TMP": temp_root})
        _run([str(vpython), "-m", "compileall", "-q", "src", "deploy"], cwd=release, env=test_env)
        _run(
            [str(vpython), "-m", "pytest", "-q", "--basetemp", str(Path(temp_root) / "pytest")],
            cwd=release,
            env=test_env,
        )


def _rewrite_env_archive_path() -> None:
    stat = ENV_FILE.stat()
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    key = "DRJAVAN_ARCHIVE_DIR="
    replacement = key + '"/opt/drjavanbot/current/گروه دکتر جوان"'
    output: list[str] = []
    inserted = False
    for line in lines:
        if line.strip().startswith(key):
            if not inserted:
                output.append(replacement)
                inserted = True
            continue
        output.append(line)
    if not inserted:
        output.append(replacement)

    fd, temp = tempfile.mkstemp(prefix=".drjavanbot.env.", dir=ENV_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, stat.st_mode & 0o777 or 0o600)
        os.chown(temp, stat.st_uid, stat.st_gid)
        os.replace(temp, ENV_FILE)
        _fsync_directory(ENV_FILE.parent)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _prepare_update_state(account, sha: str, previous_release: Path | None) -> None:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(UPDATE_DIR, account.pw_uid, account.pw_gid)
    os.chmod(UPDATE_DIR, 0o700)
    history_path = UPDATE_DIR / "history.json"
    history = _read_history(history_path)
    previous_sha = _release_marker(previous_release)
    for value in (previous_sha, sha):
        if value:
            history = [item for item in history if item != value]
            history.append(value)
    _atomic_json(history_path, history[-10:], uid=account.pw_uid, gid=account.pw_gid)


def _read_history(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and len(item) == 40]


def _release_marker(release: Path | None) -> str | None:
    if release is None:
        return None
    marker = release / ".deploy_commit"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if len(value) == 40 else None


def _active_release() -> Path | None:
    if not CURRENT.is_symlink():
        return None
    try:
        return CURRENT.resolve(strict=True)
    except OSError:
        return None


def _switch_current(release: Path) -> None:
    CURRENT.parent.mkdir(parents=True, exist_ok=True)
    temp_link = CURRENT.parent / ".current.bootstrap"
    temp_link.unlink(missing_ok=True)
    os.symlink(release, temp_link)
    os.replace(temp_link, CURRENT)
    _fsync_directory(CURRENT.parent)


def _snapshot_units() -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for name in UNIT_NAMES:
        target = SYSTEMD / name
        try:
            snapshot[name] = target.read_bytes()
        except FileNotFoundError:
            snapshot[name] = None
    return snapshot


def _install_units() -> None:
    for name in UNIT_NAMES:
        source = CONTROL_REPO / "deploy" / name
        target = SYSTEMD / name
        shutil.copy2(source, target)
        os.chmod(target, 0o644)
    _fsync_directory(SYSTEMD)


def _restore_units(snapshot: dict[str, bytes | None]) -> None:
    for name, content in snapshot.items():
        target = SYSTEMD / name
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(content)
            os.chmod(target, 0o644)


def _wait_service_active() -> None:
    deadline = time.monotonic() + 20
    stable_since: float | None = None
    while time.monotonic() < deadline:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", "drjavanbot.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        now = time.monotonic()
        if active:
            stable_since = stable_since or now
            if now - stable_since >= 3:
                return
        else:
            stable_since = None
        time.sleep(0.5)
    raise RuntimeError("drjavanbot.service did not remain active")


def _atomic_json(path: Path, value, *, uid: int, gid: int) -> None:
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.chown(temp, uid, gid)
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


def _text(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    *,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=check, timeout=timeout)


if __name__ == "__main__":
    raise SystemExit(main())
