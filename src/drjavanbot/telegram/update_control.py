from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

UPDATE_MODE_NOTIFY = "notify"
UPDATE_MODE_AUTO = "auto"
UPDATE_MODE_OFF = "off"
VALID_UPDATE_MODES = frozenset({UPDATE_MODE_NOTIFY, UPDATE_MODE_AUTO, UPDATE_MODE_OFF})

ACTIVE_UPDATE_STATES = frozenset({
    "pending", "queued", "checking", "preparing", "validating", "staging",
    "switching", "restarting", "verifying", "rolling_back", "running",
})
TERMINAL_UPDATE_STATES = frozenset({"success", "failed", "up_to_date", "rolled_back"})

_ALLOWED_ACTIONS = {"update", "rollback"}
_MAX_STATUS_BYTES = 128 * 1024
_STALE_PENDING_SECONDS = 45 * 60
_CURRENT_MARKER = Path("/opt/drjavanbot/current/.deploy_commit")
_GITHUB_MAIN_API = "https://api.github.com/repos/ArianGhsm/DrJavanGroupDatabase/commits/main"
_GITHUB_RUNS_API = "https://api.github.com/repos/ArianGhsm/DrJavanGroupDatabase/actions/runs"
_EXPECTED_WORKFLOW = "DrJavanBot tests"


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    state: str
    request_id: str | None = None
    action: str | None = None
    current_sha: str | None = None
    target_sha: str | None = None
    message: str | None = None
    updated_at: str | None = None
    stage: str | None = None
    stage_label: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    detail: str | None = None
    started_at: str | None = None
    error_id: str | None = None
    change_class: str | None = None
    ci_status: str | None = None
    duration_seconds: float | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_UPDATE_STATES

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_UPDATE_STATES


@dataclass(frozen=True, slots=True)
class RemoteUpdateInfo:
    current_sha: str | None
    latest_sha: str
    update_available: bool
    ci_status: str = "unknown"
    ci_checked_at: str | None = None
    ci_url: str | None = None

    @property
    def installable(self) -> bool:
        return self.update_available and self.ci_status == "success"


@dataclass(frozen=True, slots=True)
class ProgressBinding:
    request_id: str
    chat_id: int
    message_id: int
    target_sha: str | None = None


class UpdateControl:
    """Unprivileged control plane for the root-owned updater.

    The bot can request one of two fixed actions, inspect structured progress,
    bind a Telegram message to that progress, and verify GitHub CI. It never
    accepts shell commands, arbitrary repositories, paths, or service names.
    """

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.parent / "update"
        self.request_path = self.root / "request.json"
        self.result_path = self.root / "result.json"
        self.notification_path = self.root / "notification.json"
        self.binding_path = self.root / "progress-ui.json"
        self.remote_cache_path = self.root / "remote.json"
        self.history_events_path = self.root / "history-events.json"
        self.legacy_history_path = self.root / "history.json"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def request(
        self,
        action: str,
        *,
        target_sha: str | None = None,
        ci_status: str | None = None,
        source: str = "manual",
    ) -> str:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError("unsupported update action")
        if target_sha is not None and not _valid_sha(target_sha):
            raise ValueError("invalid target sha")
        if self.request_path.exists():
            status = self.status()
            if status.state != "stalled":
                raise RuntimeError("an update request is already pending")
            self.request_path.unlink(missing_ok=True)
        request_id = secrets.token_hex(8)
        payload = {
            # Keep schema=1 for one-release backward compatibility with the
            # pre-v2 root updater; v2 safely consumes the additional fields.
            "schema": 1,
            "request_id": request_id,
            "action": action,
            "requested_at": _now(),
            "source": source if source in {"manual", "auto"} else "manual",
        }
        if target_sha:
            payload["target_sha"] = target_sha
        if ci_status:
            payload["ci_status"] = str(ci_status)[:32]
        self._atomic_write(self.request_path, payload)
        return request_id

    def request_verified_update(self, *, source: str = "manual", timeout: float = 8.0) -> tuple[str, RemoteUpdateInfo]:
        info = self.remote_version(timeout=timeout)
        if info is None:
            raise RuntimeError("GitHub status is currently unavailable")
        if not info.update_available:
            raise RuntimeError("already up to date")
        if info.ci_status != "success":
            raise RuntimeError(f"target CI is not green: {info.ci_status}")
        request_id = self.request(
            "update",
            target_sha=info.latest_sha,
            ci_status=info.ci_status,
            source=source,
        )
        return request_id, info

    def status(self) -> UpdateStatus:
        result = self._read_json(self.result_path)
        request = self._read_json(self.request_path)
        if request is not None:
            request_id = _text(request.get("request_id"))
            if result is not None and _text(result.get("request_id")) == request_id:
                parsed = self._status_from_result(result)
                if parsed.active or parsed.terminal:
                    return parsed
            requested_at = _text(request.get("requested_at"))
            age = _age_seconds(requested_at)
            if age is not None and age > _STALE_PENDING_SECONDS:
                return UpdateStatus(
                    state="stalled",
                    stage="queued",
                    stage_label="درخواست بدون پاسخ",
                    request_id=request_id,
                    action=_text(request.get("action")),
                    target_sha=_sha(request.get("target_sha")),
                    ci_status=_text(request.get("ci_status")),
                    message="درخواست بیش از ۴۵ دقیقه بدون heartbeat مانده است.",
                    detail="نسخه فعال تغییر نکرده؛ درخواست جدید می‌تواند جایگزین شود.",
                    updated_at=requested_at,
                    started_at=requested_at,
                )
            return UpdateStatus(
                state="pending",
                stage="queued",
                stage_label="در صف اجرا",
                progress_current=0,
                progress_total=7,
                request_id=request_id,
                action=_text(request.get("action")),
                target_sha=_sha(request.get("target_sha")),
                ci_status=_text(request.get("ci_status")),
                message="درخواست ثبت شده و در صف updater است.",
                updated_at=requested_at,
                started_at=requested_at,
            )
        if result is None:
            return UpdateStatus(state="idle", current_sha=self.current_release_sha())
        return self._status_from_result(result)

    def current_release_sha(self) -> str | None:
        try:
            value = _CURRENT_MARKER.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value if _valid_sha(value) else None

    def remote_version(self, *, timeout: float = 8.0) -> RemoteUpdateInfo | None:
        payload = self._github_json(_GITHUB_MAIN_API, timeout=timeout)
        latest = payload.get("sha") if isinstance(payload, dict) else None
        if not isinstance(latest, str) or not _valid_sha(latest):
            return None
        current = self.current_release_sha()
        ci_status, ci_url = self._ci_status(latest, timeout=timeout)
        info = RemoteUpdateInfo(
            current_sha=current,
            latest_sha=latest,
            update_available=current != latest,
            ci_status=ci_status,
            ci_checked_at=_now(),
            ci_url=ci_url,
        )
        self._atomic_write(self.remote_cache_path, {
            "schema": 2,
            "current_sha": current,
            "latest_sha": latest,
            "update_available": current != latest,
            "ci_status": ci_status,
            "ci_checked_at": info.ci_checked_at,
            "ci_url": ci_url,
        })
        return info

    def cached_remote_version(self, *, max_age_seconds: float = 900.0) -> RemoteUpdateInfo | None:
        payload = self._read_json(self.remote_cache_path)
        if payload is None:
            return None
        checked = _text(payload.get("ci_checked_at"))
        age = _age_seconds(checked)
        if age is None or age > max_age_seconds:
            return None
        latest = _sha(payload.get("latest_sha"))
        if latest is None:
            return None
        current = _sha(payload.get("current_sha"))
        return RemoteUpdateInfo(
            current_sha=current,
            latest_sha=latest,
            update_available=bool(payload.get("update_available", current != latest)),
            ci_status=_text(payload.get("ci_status")) or "unknown",
            ci_checked_at=checked,
            ci_url=_text(payload.get("ci_url")),
        )

    def _ci_status(self, sha: str, *, timeout: float) -> tuple[str, str | None]:
        query = urlencode({"head_sha": sha, "event": "push", "per_page": 20})
        payload = self._github_json(f"{_GITHUB_RUNS_API}?{query}", timeout=timeout)
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            return "unknown", None
        matching = [
            run for run in runs
            if isinstance(run, dict)
            and str(run.get("head_sha") or "") == sha
            and str(run.get("name") or "") == _EXPECTED_WORKFLOW
        ]
        if not matching:
            return "unknown", None
        matching.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
        run = matching[0]
        url = _text(run.get("html_url"))
        status = str(run.get("status") or "").casefold()
        conclusion = str(run.get("conclusion") or "").casefold()
        if status in {"queued", "in_progress", "waiting", "pending", "requested"}:
            return "pending", url
        if status == "completed" and conclusion == "success":
            return "success", url
        if status == "completed":
            return "failure", url
        return "unknown", url

    def _github_json(self, url: str, *, timeout: float) -> dict:
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "DrJavanBot-update-control/2",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if int(getattr(response, "status", 200)) != 200:
                    return {}
                payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {}
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            return {}

    def claim_update_notification(self, sha: str, kind: str = "available") -> bool:
        if not _valid_sha(sha):
            return False
        key = f"{sha}:{str(kind)[:40]}"
        existing = self._read_json(self.notification_path) or {}
        claims = existing.get("claims")
        if not isinstance(claims, list):
            # Migrate the old single-SHA shape without re-notifying it.
            claims = []
            old_sha = _sha(existing.get("sha"))
            if old_sha:
                claims.append(f"{old_sha}:available")
        if key in claims:
            return False
        claims.append(key)
        self._atomic_write(self.notification_path, {
            "schema": 2,
            "claims": claims[-40:],
            "updated_at": _now(),
        })
        return True

    def bind_progress_message(self, request_id: str, chat_id: int, message_id: int, *, target_sha: str | None = None) -> None:
        if not request_id or int(chat_id) <= 0 or int(message_id) <= 0:
            return
        self._atomic_write(self.binding_path, {
            "schema": 1,
            "request_id": str(request_id)[:64],
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "target_sha": target_sha if target_sha and _valid_sha(target_sha) else None,
            "updated_at": _now(),
        })

    def progress_binding(self) -> ProgressBinding | None:
        payload = self._read_json(self.binding_path)
        if payload is None:
            return None
        try:
            request_id = str(payload.get("request_id") or "")
            chat_id = int(payload.get("chat_id"))
            message_id = int(payload.get("message_id"))
        except (TypeError, ValueError):
            return None
        if not request_id or chat_id <= 0 or message_id <= 0:
            return None
        return ProgressBinding(
            request_id=request_id,
            chat_id=chat_id,
            message_id=message_id,
            target_sha=_sha(payload.get("target_sha")),
        )

    def clear_progress_binding(self, request_id: str | None = None) -> None:
        binding = self.progress_binding()
        if binding is None:
            return
        if request_id is None or binding.request_id == request_id:
            self.binding_path.unlink(missing_ok=True)

    def history(self, *, limit: int = 10) -> tuple[dict, ...]:
        limit = max(1, min(int(limit), 20))
        payload = self._read_json_value(self.history_events_path)
        if isinstance(payload, list):
            items = [item for item in payload if isinstance(item, dict)]
            return tuple(items[-limit:][::-1])
        # Compatibility view for legacy SHA-only history.
        legacy = self._read_json_value(self.legacy_history_path)
        if isinstance(legacy, list):
            rows = [
                {"target_sha": value, "result": "healthy", "action": "release"}
                for value in legacy if isinstance(value, str) and _valid_sha(value)
            ]
            return tuple(rows[-limit:][::-1])
        return ()

    def previous_release_sha(self) -> str | None:
        current = self.current_release_sha()
        for item in self.history(limit=20):
            sha = _sha(item.get("target_sha"))
            if sha and sha != current:
                return sha
        return None

    def _status_from_result(self, result: dict) -> UpdateStatus:
        return UpdateStatus(
            state=_text(result.get("state")) or "unknown",
            request_id=_text(result.get("request_id")),
            action=_text(result.get("action")),
            current_sha=_sha(result.get("current_sha")),
            target_sha=_sha(result.get("target_sha")),
            message=_text(result.get("message")),
            updated_at=_text(result.get("updated_at")),
            stage=_text(result.get("stage")),
            stage_label=_text(result.get("stage_label")),
            progress_current=_bounded_int(result.get("progress_current"), 0, 99),
            progress_total=_bounded_int(result.get("progress_total"), 0, 99),
            detail=_text(result.get("detail")),
            started_at=_text(result.get("started_at")),
            error_id=_text(result.get("error_id")),
            change_class=_text(result.get("change_class")),
            ci_status=_text(result.get("ci_status")),
            duration_seconds=_float_or_none(result.get("duration_seconds")),
        )

    def _read_json(self, path: Path) -> dict | None:
        value = self._read_json_value(path)
        return value if isinstance(value, dict) else None

    def _read_json_value(self, path: Path):
        try:
            if not path.is_file() or path.stat().st_size > _MAX_STATUS_BYTES:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _atomic_write(self, path: Path, payload) -> None:
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


def _sha(value) -> str | None:
    text = str(value or "").strip()
    return text if _valid_sha(text) else None


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


def _bounded_int(value, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def _float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ACTIVE_UPDATE_STATES", "TERMINAL_UPDATE_STATES", "UPDATE_MODE_AUTO",
    "UPDATE_MODE_NOTIFY", "UPDATE_MODE_OFF", "VALID_UPDATE_MODES",
    "ProgressBinding", "RemoteUpdateInfo", "UpdateControl", "UpdateStatus",
]
