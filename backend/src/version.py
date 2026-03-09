from __future__ import annotations

import os
from pathlib import Path

_FALLBACK_VERSION = "0.0.0"


def _read_app_version() -> str:
    from_env = str(os.getenv("BLABS_APP_VERSION", "")).strip()
    if from_env:
        return from_env

    candidates = (
        Path("/app/VERSION"),
        Path(__file__).resolve().parents[2] / "VERSION",
    )
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return _FALLBACK_VERSION


APP_VERSION = _read_app_version()
