#!/usr/bin/env python3
"""Stable systemd entrypoint for Admin Control Center v2.

The privileged unit keeps this historical path while the implementation lives in
`update_engine_v2.py`.  Execute that source in this module namespace instead of
import/re-exporting it: operational tooling that intentionally monkeypatches fixed
paths/functions for offline verification still observes the same globals, while
production has exactly one engine implementation to maintain.
"""
from __future__ import annotations

from pathlib import Path

_ENGINE = Path(__file__).resolve().with_name("update_engine_v2.py")
exec(compile(_ENGINE.read_text(encoding="utf-8"), str(_ENGINE), "exec"), globals(), globals())
