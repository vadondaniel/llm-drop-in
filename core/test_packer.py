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
    discover_latest_package,
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

    def test_worktree_package_uses_canonical_name_and_replaces_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            first = package_project(root, scope=PACKAGE_WORKTREE)
            self.assertEqual(first.path.name, f"{root.name}.zip")
            self.assertTrue(first.path.is_file())
            first_bytes = first.path.read_bytes()

            (root / "tracked.txt").write_bytes(b"updated\n")
            second = package_project(root, scope=PACKAGE_WORKTREE)

            self.assertEqual(second.path, first.path)
            self.assertEqual(second.path.name, f"{root.name}.zip")
            self.assertTrue(second.path.is_file())
            self.assertNotEqual(second.path.read_bytes(), first_bytes)
            self.assertEqual(
                sorted(p.name for p in root.parent.glob(f"{root.name}*.zip")),
                [f"{root.name}.zip"],
            )

    def test_scope_specific_packages_use_clear_names_and_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)

            worktree = package_project(root, scope=PACKAGE_WORKTREE)
            staged = package_project(root, scope=PACKAGE_STAGED)
            head = package_project(root, scope=PACKAGE_HEAD)

            self.assertEqual(worktree.path.name, f"{root.name}.zip")
            self.assertEqual(staged.path.name, f"{root.name}-staged.zip")
            self.assertEqual(head.path.name, f"{root.name}-head.zip")
            self.assertTrue(worktree.path.exists())
            self.assertTrue(staged.path.exists())
            self.assertTrue(head.path.exists())

    def test_successful_replacement_leaves_no_temp_zip_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            result = package_project(root)

            self.assertTrue(result.path.exists())
            temp_entries = [
                p for p in root.parent.iterdir()
                if p.is_dir() and p.name.startswith(f".{result.path.stem}-")
            ]
            self.assertEqual(temp_entries, [])

    def test_old_generated_names_are_removed_after_successful_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            old_names = [
                f"{root.name}-v7-worktree.zip",
                f"{root.name}-worktree-v8.zip",
                f"{root.name}-llm-worktree-v9-20260919-120000.zip",
                f"{root.name}-llm-20260919-120000.zip",
            ]
            for name in old_names:
                (root.parent / name).write_bytes(b"old package")

            result = package_project(root, scope=PACKAGE_WORKTREE)

            self.assertEqual(result.path.name, f"{root.name}.zip")
            for name in old_names:
                self.assertFalse((root.parent / name).exists())

    def test_discover_latest_package_finds_newest_scope_for_current_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            worktree = package_project(root, scope=PACKAGE_WORKTREE)
            head = package_project(root, scope=PACKAGE_HEAD)
            import os
            os.utime(worktree.path, ns=(1_000_000_000, 1_000_000_000))
            os.utime(head.path, ns=(2_000_000_000, 2_000_000_000))

            discovered = discover_latest_package(root)

            self.assertIsNotNone(discovered)
            assert discovered is not None
            self.assertEqual(discovered.path, head.path)
            self.assertEqual(discovered.scope, PACKAGE_HEAD)
            self.assertEqual(discovered.files, head.files)
            self.assertEqual(discovered.size_bytes, head.size_bytes)

    def test_discover_ignores_corrupt_canonical_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            corrupt = root.parent / f"{root.name}.zip"
            corrupt.write_bytes(b"not a zip")

            self.assertIsNone(discover_latest_package(root))

    def test_default_output_is_next_to_project_not_inside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            result = package_project(root)
            self.assertEqual(result.path.parent, root.parent)
            self.assertNotEqual(result.path.parent, root)
            self.assertTrue(result.path.is_file())
