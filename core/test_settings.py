
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.settings as settings_module


class SettingsTests(unittest.TestCase):
    def test_download_monitor_defaults_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_module, "SETTINGS_FILE", settings_path):
                with mock.patch.object(settings_module, "APP_DIR", Path(tmp)):
                    settings = settings_module.load_settings()

            self.assertFalse(settings["download_monitor_enabled"])
            self.assertEqual(
                settings["download_folder"], str(Path.home() / "Downloads")
            )

    def test_invalid_download_monitor_settings_are_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp)
            settings_path = app_dir / "settings.json"
            settings_path.write_text(
                json.dumps({
                    "project": "",
                    "recent_projects": [],
                    "download_monitor_enabled": "yes",
                    "download_folder": "",
                }),
                encoding="utf-8",
            )
            with mock.patch.object(settings_module, "SETTINGS_FILE", settings_path):
                with mock.patch.object(settings_module, "APP_DIR", app_dir):
                    settings = settings_module.load_settings()

            self.assertFalse(settings["download_monitor_enabled"])
            self.assertEqual(
                settings["download_folder"], str(Path.home() / "Downloads")
            )
