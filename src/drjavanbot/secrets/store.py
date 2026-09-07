from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
from typing import Protocol

_SECRET_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
AVALAI_API_KEY_SECRET = "avalai_api_key"


class SecretStore(Protocol):
    def get_secret(self, name: str) -> str | None: ...
    def set_secret(self, name: str, value: str) -> None: ...
    def delete_secret(self, name: str) -> bool: ...
    def is_configured(self, name: str) -> bool: ...


class LocalFileSecretStore:
    """Plain local secret files protected by filesystem permissions.

    Deliberately avoids fake in-process encryption. Production should protect
    this directory with a dedicated Unix user and 0700/0600 permissions.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def get_secret(self, name: str) -> str | None:
        path = self._path(name)
        try:
            value = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        value = value.rstrip("\r\n")
        return value or None

    def set_secret(self, name: str, value: str) -> None:
        if not value or "\n" in value or "\r" in value:
            raise ValueError("secret value is empty or malformed")
        path = self._path(name)
        fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            Path(temp_name).unlink(missing_ok=True)
            raise

    def delete_secret(self, name: str) -> bool:
        path = self._path(name)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def is_configured(self, name: str) -> bool:
        return self.get_secret(name) is not None

    def _path(self, name: str) -> Path:
        if not _SECRET_NAME_RE.fullmatch(name):
            raise ValueError("invalid secret name")
        return self.root / name

    def __repr__(self) -> str:
        return f"LocalFileSecretStore(root={self.root!r})"
