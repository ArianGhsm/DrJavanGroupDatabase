#!/usr/bin/env python3
"""Compatibility entrypoint for Admin Control Center v2 updater.

The systemd unit intentionally keeps the stable self_update.py path.  The actual
engine lives in update_engine_v2.py so its state machine can evolve without
changing the privileged unit target.
"""
from __future__ import annotations

from pathlib import Path
import sys

_DEPLOY_DIR = Path(__file__).resolve().parent
if str(_DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_DIR))

import update_engine_v2 as _engine  # noqa: E402

# Re-export the fixed, testable updater surface for existing operational tests and
# bootstrap tooling. Underscore helpers remain private to the project, not user
# controllable APIs.
for _name in dir(_engine):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_engine, _name)


if __name__ == "__main__":
    raise SystemExit(_engine.main())
