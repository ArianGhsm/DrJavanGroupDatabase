from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import tempfile

_ALLOWED_ACTIONS = {"update", "rollback"}
_MAX_STATUS_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    state: str
    request_id: str | None = None
    action: str | None = None
    current_sha: str | None = None
    target_sha: str | None = None
    message: str | None = None
    updated_at: str | None = None


class UpdateControl:
    """Unprivileged bot-side control plane for the root-owned updater.

    The bot can only write a fixed-format request file. It never receives a shell
    command, repository URL, branch, path or service name from Telegram input.
    A root-owned systemd.path unit observes the request file and invokes the
    hard-coded updater service.
    """

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.parent / "update"
        self.request_path = self.root / "request.json"
        self.result_path = self.root / "result.json"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def request(self, action: str) -> str:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("unsupported update action")
        if self.request_path.exists():
            raise RuntimeError("an update request is already pending")
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
            return UpdateStatus(
                state="pending",
                request_id=_text(request.get("request_id")),
                action=_text(request.get("action")),
                message="درخواست در صف اجراست.",
                updated_at=_text(request.get("requested_at")),
            )
        if result is None:
            return UpdateStatus(state="idle")
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


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:500] if text else None
