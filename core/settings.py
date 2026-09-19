from __future__ import annotations

import json
from pathlib import Path


APP_DIR = Path.home() / "AppData" / "Local" / "LLM-Drop-In"
SETTINGS_FILE = APP_DIR / "settings.json"
HISTORY_FILE = APP_DIR / "history.json"
TRANSACTION_DIR = APP_DIR / "transactions"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_settings() -> dict:
    settings = _load(SETTINGS_FILE, {
        "project": "",
        "recent_projects": [],
        "download_monitor_enabled": False,
        "download_folder": str(Path.home() / "Downloads"),
    })
    if not isinstance(settings, dict):
        settings = {}
    recent = settings.get("recent_projects", [])
    if not isinstance(recent, list):
        recent = []
    cleaned = []
    seen = set()
    for value in recent:
        if not isinstance(value, str) or not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    settings["recent_projects"] = cleaned[:15]
    settings.setdefault("project", cleaned[0] if cleaned else "")
    if not isinstance(settings.get("download_monitor_enabled"), bool):
        settings["download_monitor_enabled"] = False
    download_folder = settings.get("download_folder")
    if not isinstance(download_folder, str) or not download_folder:
        settings["download_folder"] = str(Path.home() / "Downloads")
    return settings


def remember_project(settings: dict, project: Path, limit: int = 15) -> None:
    path = str(project.resolve())
    recent = settings.get("recent_projects", [])
    if not isinstance(recent, list):
        recent = []
    recent = [
        value for value in recent
        if isinstance(value, str) and value.casefold() != path.casefold()
    ]
    settings["recent_projects"] = [path, *recent][:limit]
    settings["project"] = path


def save_settings(settings: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def load_history() -> list[dict]:
    return _load(HISTORY_FILE, [])


def add_history(entry: dict, limit: int = 30) -> list[dict]:
    history = load_history()
    history.insert(0, entry)
    kept = history[:limit]
    APP_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return kept


def update_history_entry(history_id: str, **changes) -> bool:
    history = load_history()
    for entry in history:
        if entry.get("id") == history_id:
            entry.update(changes)
            APP_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps(
                history, indent=2), encoding="utf-8")
            return True
    return False
