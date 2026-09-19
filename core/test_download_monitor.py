from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from core.download_monitor import DownloadFolderMonitor


class DownloadMonitorTests(unittest.TestCase):
    @staticmethod
    def _zip(path: Path, content: bytes = b"hello") -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("hello.txt", content)

    def test_existing_zip_is_not_reported_when_monitor_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._zip(folder / "already-here.zip")
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()

            self.assertEqual(monitor.poll(now=10.0), [])
            self.assertEqual(monitor.pending, ())

    def test_new_zip_is_reported_only_after_stable_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()
            path = folder / "download.zip"
            self._zip(path)

            self.assertEqual(monitor.poll(now=20.0), [])
            self.assertEqual(monitor.poll(now=20.9), [])
            self.assertEqual(monitor.poll(now=21.01), [path.resolve()])
            self.assertEqual(monitor.pending, ())

    def test_signature_change_resets_stability_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()
            path = folder / "download.zip"
            self._zip(path, b"one")

            self.assertEqual(monitor.poll(now=30.0), [])
            self._zip(path, b"two")
            self.assertEqual(monitor.poll(now=30.9), [])
            self.assertEqual(monitor.poll(now=31.8), [])
            self.assertEqual(monitor.poll(now=31.91), [path.resolve()])

    def test_partial_download_companion_delays_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()
            path = folder / "download.zip"
            self._zip(path)
            partial = Path(str(path) + ".crdownload")
            partial.write_bytes(b"partial")

            self.assertEqual(monitor.poll(now=40.0), [])
            self.assertEqual(monitor.poll(now=41.1), [])
            partial.unlink()
            self.assertEqual(monitor.poll(now=41.9), [path.resolve()])

    def test_corrupt_zip_is_retried_until_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(
                folder, stable_for=1.0, retry_every=0.5)
            monitor.start()
            path = folder / "download.zip"
            path.write_bytes(b"not a zip")

            self.assertEqual(monitor.poll(now=50.0), [])
            self.assertEqual(monitor.poll(now=51.0), [])
            self.assertEqual(monitor.poll(now=51.5), [])

            self._zip(path)
            self.assertEqual(monitor.poll(now=51.6), [])
            self.assertEqual(monitor.poll(now=52.61), [path.resolve()])

    def test_non_zip_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()
            (folder / "notes.txt").write_text("not a zip", encoding="utf-8")
            (folder / "download.crdownload").write_bytes(b"partial")

            self.assertEqual(monitor.poll(now=60.0), [])
            self.assertEqual(monitor.pending, ())

    def test_deleted_pending_zip_is_forgotten(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()
            path = folder / "download.zip"
            self._zip(path)
            self.assertEqual(monitor.poll(now=70.0), [])
            path.unlink()

            self.assertEqual(monitor.poll(now=70.5), [])
            self.assertEqual(monitor.pending, ())

    def test_replaced_zip_at_same_path_is_detected_as_new_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "download.zip"
            self._zip(path, b"old")
            monitor = DownloadFolderMonitor(folder, stable_for=1.0)
            monitor.start()

            path.unlink()
            self._zip(path, b"new")
            self.assertEqual(monitor.poll(now=80.0), [])
            self.assertEqual(monitor.poll(now=81.01), [path.resolve()])

    def test_candidate_queue_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = DownloadFolderMonitor(folder, stable_for=0.5)
            monitor.start()
            for name in ("z.zip", "a.zip", "m.zip"):
                self._zip(folder / name)

            self.assertEqual(monitor.poll(now=90.0), [])
            self.assertEqual(
                monitor.poll(now=90.51),
                [
                    (folder / "a.zip").resolve(),
                    (folder / "m.zip").resolve(),
                    (folder / "z.zip").resolve(),
                ],
            )


if __name__ == "__main__":
    unittest.main()
