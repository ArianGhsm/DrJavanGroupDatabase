#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile

CONTROL_REPO = Path("/opt/drjavanbot/app")
RELEASES = Path("/opt/drjavanbot/releases")
CURRENT = Path("/opt/drjavanbot/current")
ENV_FILE = Path("/etc/drjavanbot/drjavanbot.env")
UPDATE_DIR = Path("/var/lib/drjavanbot/update")
SYSTEMD = Path("/etc/systemd/system")
EXPECTED_REMOTES = {
    "https://github.com/ArianGhsm/DrJavanGroupDatabase.git",
    "https://github.com/ArianGhsm/DrJavanGroupDatabase",
    "git@github.com:ArianGhsm/DrJavanGroupDatabase.git",
}


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("bootstrap must run as root")
    remote = _text(["git", "-C", str(CONTROL_REPO), "remote", "get-url", "origin"]).strip()
    if remote not in EXPECTED_REMOTES:
        raise SystemExit("unexpected repository origin")
    sha = _text(["git", "-C", str(CONTROL_REPO), "rev-parse", "HEAD"]).strip()

    RELEASES.mkdir(parents=True, exist_ok=True)
    release = RELEASES / sha
    if not release.exists():
        _run(["git", "-C", str(CONTROL_REPO), "worktree", "add", "--detach", str(release), sha])
    python_exe = shutil.which("python3.11") or shutil.which("python3")
    if not python_exe:
        raise SystemExit("Python 3.11+ is required")
    version = _text([python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]).strip()
    major, minor = (int(x) for x in version.split(".", 1))
    if (major, minor) < (3, 11):
        raise SystemExit("Python 3.11+ is required")

    venv = release / ".venv"
    if not (venv / "bin/python").exists():
        _run([python_exe, "-m", "venv", str(venv)])
        _run([str(venv / "bin/python"), "-m", "pip", "install", "-r", "requirements.lock"], cwd=release)
        _run([str(venv / "bin/python"), "-m", "pip", "install", "-r", "requirements-dev.lock"], cwd=release)
        _run([str(venv / "bin/python"), "-m", "pip", "install", "--no-deps", "."], cwd=release)

    # Never inherit a stale pytest temp tree from another/root process.
    with tempfile.TemporaryDirectory(prefix="drjavanbot-bootstrap-", dir="/var/tmp") as temp_root:
        test_env = dict(os.environ)
        test_env.update({"TMPDIR": temp_root, "TEMP": temp_root, "TMP": temp_root})
        _run(
            [str(venv / "bin/python"), "-m", "pytest", "-q", "--basetemp", str(Path(temp_root) / "pytest")],
            cwd=release,
            env=test_env,
        )
    (release / ".deploy_commit").write_text(sha + "\n", encoding="utf-8")

    temp_link = CURRENT.parent / ".current.bootstrap"
    temp_link.unlink(missing_ok=True)
    os.symlink(release, temp_link)
    os.replace(temp_link, CURRENT)

    _rewrite_env_archive_path()
    account = pwd.getpwnam("drjavanbot")
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(UPDATE_DIR, account.pw_uid, account.pw_gid)
    os.chmod(UPDATE_DIR, 0o700)
    _atomic_json(UPDATE_DIR / "history.json", [sha], uid=account.pw_uid, gid=account.pw_gid)

    for name in ("drjavanbot.service", "drjavanbot-updater.service", "drjavanbot-updater.path"):
        source = CONTROL_REPO / "deploy" / name
        target = SYSTEMD / name
        shutil.copy2(source, target)
        os.chmod(target, 0o644)

    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", "drjavanbot-updater.path"])
    _run(["systemctl", "restart", "drjavanbot.service"])
    _run(["systemctl", "is-active", "--quiet", "drjavanbot.service"])
    print(f"self-updater bootstrap complete at {sha[:12]}")
    return 0


def _rewrite_env_archive_path() -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    key = "DRJAVAN_ARCHIVE_DIR="
    # Keep this value valid for both systemd EnvironmentFile parsing and shell sourcing.
    replacement = key + '"/opt/drjavanbot/current/گروه دکتر جوان"'
    found = False
    output = []
    for line in lines:
        if line.strip().startswith(key):
            output.append(replacement)
            found = True
        else:
            output.append(line)
    if not found:
        output.append(replacement)
    mode = ENV_FILE.stat().st_mode & 0o777
    fd, temp = tempfile.mkstemp(prefix=".drjavanbot.env.", dir=ENV_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode or 0o600)
        os.replace(temp, ENV_FILE)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


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
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _text(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
