from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.packer import (
    PACKAGE_HEAD,
    PACKAGE_STAGED,
    PACKAGE_WORKTREE,
    PackageError,
    package_project,
)


@unittest.skipUnless(shutil.which("git"), "Git is required for package tests")
class PackageTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        ).stdout

    def _repo(self, tmp: str) -> Path:
        root = Path(tmp) / "project"
        root.mkdir()
        self._git(root, "init", "-q")
        self._git(root, "config", "user.name", "Test User")
        self._git(root, "config", "user.email", "test@example.com")
        (root / ".gitignore").write_text(
            "ignored/\n*.log\n", encoding="utf-8"
        )
        (root / "tracked.txt").write_bytes(b"committed\n")
        (root / "keep.txt").write_bytes(b"keep\n")
        (root / "ignored").mkdir()
        (root / "ignored" / "secret.txt").write_bytes(b"ignored\n")
        self._git(root, "add", ".")
        self._git(root, "commit", "-qm", "initial")
        return root

    @staticmethod
    def _read_zip(path: Path) -> dict[str, bytes]:
        with zipfile.ZipFile(path) as archive:
            return {
                item.filename: archive.read(item)
                for item in archive.infolist()
                if not item.is_dir()
            }

    def test_worktree_includes_current_tracked_and_nonignored_untracked_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "tracked.txt").write_bytes(b"working tree\n")
            (root / "new.txt").write_bytes(b"new\n")
            (root / "debug.log").write_bytes(b"ignored\n")
            (root / "ignored" / "new.txt").write_bytes(b"ignored\n")

            result = package_project(root, scope=PACKAGE_WORKTREE)
            files = self._read_zip(result.path)

            self.assertEqual(
                set(files),
                {".gitignore", "keep.txt", "new.txt", "tracked.txt"},
            )
            self.assertEqual(files["tracked.txt"], b"working tree\n")
            self.assertEqual(files["new.txt"], b"new\n")
            self.assertEqual(result.files, 4)

    def test_head_does_not_apply_git_export_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".gitattributes").write_bytes(b"keep.txt export-ignore\n")
            self._git(root, "add", ".gitattributes")
            self._git(root, "commit", "-qm", "attributes")

            result = package_project(root, scope=PACKAGE_HEAD)
            files = self._read_zip(result.path)

            self.assertIn("keep.txt", files)

    def test_head_contains_committed_state_not_worktree_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "tracked.txt").write_bytes(b"unstaged\n")
            (root / "new.txt").write_bytes(b"untracked\n")

            result = package_project(root, scope=PACKAGE_HEAD)
            files = self._read_zip(result.path)

            self.assertEqual(
                set(files),
                {".gitignore", "keep.txt", "tracked.txt"},
            )
            self.assertEqual(files["tracked.txt"], b"committed\n")

    def test_staged_contains_index_state_but_not_unstaged_or_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "tracked.txt").write_bytes(b"staged\n")
            self._git(root, "add", "tracked.txt")
            (root / "tracked.txt").write_bytes(b"unstaged after add\n")
            (root / "new.txt").write_bytes(b"untracked\n")

            result = package_project(root, scope=PACKAGE_STAGED)
            files = self._read_zip(result.path)

            self.assertEqual(
                set(files),
                {".gitignore", "keep.txt", "tracked.txt"},
            )
            self.assertEqual(files["tracked.txt"], b"staged\n")

    def test_staged_deletion_is_reflected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "keep.txt").unlink()
            self._git(root, "add", "-u")

            result = package_project(root, scope=PACKAGE_STAGED)
            files = self._read_zip(result.path)

            self.assertNotIn("keep.txt", files)

    def test_worktree_reports_missing_tracked_files_without_inventing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / "keep.txt").unlink()

            result = package_project(root, scope=PACKAGE_WORKTREE)
            files = self._read_zip(result.path)

            self.assertNotIn("keep.txt", files)
            self.assertIn("keep.txt", result.missing)

    def test_non_git_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-a-repo"
            root.mkdir()
            with self.assertRaises(PackageError):
                package_project(root)

    def test_default_output_is_next_to_project_not_inside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            result = package_project(root)
            self.assertEqual(result.path.parent, root.parent)
            self.assertNotEqual(result.path.parent, root)
            self.assertTrue(result.path.is_file())
