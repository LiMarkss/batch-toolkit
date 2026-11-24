"""Persisted user settings (JSON in the user's profile folder)."""
from __future__ import annotations

import json
import os
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "BatchToolkit"


def default_settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings(path: str | os.PathLike | None = None) -> dict:
    """Load settings; returns {} when missing or corrupted (never raises)."""
    path = Path(path) if path else default_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict, path: str | os.PathLike | None = None) -> Path:
    """Atomically write settings JSON."""
    path = Path(path) if path else default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
