from __future__ import annotations

import json
from pathlib import Path

APP_DIR = Path.home() / "AppData" / "Local" / "LLM-Drop-In"
SETTINGS_FILE = APP_DIR / "settings.json"
HISTORY_FILE = APP_DIR / "history.json"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_settings() -> dict:
    return _load(SETTINGS_FILE, {"project": ""})


def save_settings(settings: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def load_history() -> list[dict]:
    return _load(HISTORY_FILE, [])


def add_history(entry: dict, limit: int = 30) -> None:
    history = load_history()
    history.insert(0, entry)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(
        history[:limit], indent=2), encoding="utf-8")
