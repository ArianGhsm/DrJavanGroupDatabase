from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_ALLOWED_ACTIONS = {"update", "rollback"}
_MAX_STATUS_BYTES = 64 * 1024
_STALE_PENDING_SECONDS = 45 * 60
_CURRENT_MARKER = Path("/opt/drjavanbot/current/.deploy_commit")
_GITHUB_MAIN_API = "https://api.github.com/repos/ArianGhsm/DrJavanGroupDatabase/commits/main"


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    state: str
    request_id: str | None = None
    action: str | None = None
    current_sha: str | None = None
    target_sha: str | None = None
    message: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteUpdateInfo:
    current_sha: str | None
    latest_sha: str
    update_available: bool


class UpdateControl:
    """Unprivileged bot-side control plane for the root-owned updater."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.parent / "update"
        self.request_path = self.root / "request.json"
        self.result_path = self.root / "result.json"
        self.notification_path = self.root / "notification.json"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def request(self, action: str) -> str:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("unsupported update action")
        if self.request_path.exists():
            status = self.status()
            if status.state != "stalled":
                raise RuntimeError("an update request is already pending")
            # systemd updater has a 30 minute timeout; after 45 minutes with no
            # fresh running heartbeat the request is stale and safe to replace.
            self.request_path.unlink(missing_ok=True)
        request_id = secrets.token_hex(8)
        payload = {
            "schema": 1,
            "request_id": request_id,
            "action": action,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        self._atomic_write(self.request_path, payload)
        return request_id

    def status(self) -> UpdateStatus:
        result = self._read_json(self.result_path)
        request = self._read_json(self.request_path)
        if request is not None:
            request_id = _text(request.get("request_id"))
            if (
                result is not None
                and _text(result.get("request_id")) == request_id
                and _text(result.get("state")) == "running"
            ):
                return self._status_from_result(result)
            requested_at = _text(request.get("requested_at"))
            if _age_seconds(requested_at) is not None and _age_seconds(requested_at) > _STALE_PENDING_SECONDS:
                return UpdateStatus(
                    state="stalled",
                    request_id=request_id,
                    action=_text(request.get("action")),
                    message="درخواست بیش از ۴۵ دقیقه بدون heartbeat مانده و stale محسوب می‌شود.",
                    updated_at=requested_at,
                )
            return UpdateStatus(
                state="pending",
                request_id=request_id,
                action=_text(request.get("action")),
                message="درخواست در صف اجرای updater است.",
                updated_at=requested_at,
            )
        if result is None:
            return UpdateStatus(state="idle")
        return self._status_from_result(result)

    def current_release_sha(self) -> str | None:
        try:
            value = _CURRENT_MARKER.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value if _valid_sha(value) else None

    def remote_version(self, *, timeout: float = 8.0) -> RemoteUpdateInfo | None:
        request = Request(
            _GITHUB_MAIN_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "DrJavanBot-update-check/1"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if int(getattr(response, "status", 200)) != 200:
                    return None
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            return None
        latest = payload.get("sha") if isinstance(payload, dict) else None
        if not isinstance(latest, str) or not _valid_sha(latest):
            return None
        current = self.current_release_sha()
        return RemoteUpdateInfo(current_sha=current, latest_sha=latest, update_available=current != latest)

    def claim_update_notification(self, sha: str) -> bool:
        if not _valid_sha(sha):
            return False
        existing = self._read_json(self.notification_path)
        if existing is not None and _text(existing.get("sha")) == sha:
            return False
        self._atomic_write(
            self.notification_path,
            {"schema": 1, "sha": sha, "notified_at": datetime.now(timezone.utc).isoformat()},
        )
        return True

    def _status_from_result(self, result: dict) -> UpdateStatus:
        return UpdateStatus(
            state=_text(result.get("state")) or "unknown",
            request_id=_text(result.get("request_id")),
            action=_text(result.get("action")),
            current_sha=_text(result.get("current_sha")),
            target_sha=_text(result.get("target_sha")),
            message=_text(result.get("message")),
            updated_at=_text(result.get("updated_at")),
        )

    def _read_json(self, path: Path) -> dict | None:
        try:
            if not path.is_file() or path.stat().st_size > _MAX_STATUS_BYTES:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _atomic_write(self, path: Path, payload: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def _valid_sha(value: str) -> bool:
    return len(value) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in value)


def _age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:500] if text else None
