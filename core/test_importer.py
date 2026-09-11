from __future__ import annotations

import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

from core.importer import apply_import, enrich_plan_hashes, make_plan, undo_import
from core.zip_analyzer import analyze


class ImporterTests(unittest.TestCase):
    def test_new_and_replace_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "existing.txt").write_text("old", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("wrapper/existing.txt", "new")
                zf.writestr("wrapper/new.txt", "created")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            self.assertEqual({p.action for p in plan}, {"replace", "new"})
            applied, transaction_id = apply_import(
                zip_path, plan, project_root=root)
            self.assertEqual(applied, 2)
            self.assertIsNotNone(transaction_id)
            self.assertEqual(
                (root / "existing.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual(
                (root / "new.txt").read_text(encoding="utf-8"), "created")

    def test_unchanged_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "same.txt"
            target.write_text("same", encoding="utf-8")
            before = target.stat().st_mtime_ns
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("same.txt", "same")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            self.assertEqual(plan[0].action, "unchanged")
            applied, transaction_id = apply_import(
                zip_path, plan, project_root=root)
            self.assertEqual(applied, 0)
            self.assertIsNone(transaction_id)
            self.assertEqual(target.stat().st_mtime_ns, before)

    def test_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            zip_path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                analyze(zip_path, root)

    def test_failed_commit_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "one.txt").write_text("old-one", encoding="utf-8")
            (root / "two.txt").write_text("old-two", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("one.txt", "new-one")
                zf.writestr("two.txt", "new-two")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))

            import core.importer as importer
            original_replace = importer.os.replace
            target_two = (root / "two.txt").resolve()
            calls = {"targets": 0}

            def failing_replace(src, dst):
                dst_path = Path(dst).resolve()
                # The first two os.replace calls commit the staged files. Fail on the second target commit.
                if dst_path == target_two and calls["targets"] == 1:
                    calls["targets"] += 1
                    raise OSError("simulated commit failure")
                if dst_path.parent == root and dst_path.name in {"one.txt", "two.txt"}:
                    calls["targets"] += 1
                return original_replace(src, dst)

            with mock.patch.object(importer.os, "replace", side_effect=failing_replace):
                with self.assertRaises(OSError):
                    apply_import(zip_path, plan)

            self.assertEqual(
                (root / "one.txt").read_text(encoding="utf-8"), "old-one")
            self.assertEqual(
                (root / "two.txt").read_text(encoding="utf-8"), "old-two")

    def test_external_change_after_analysis_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "existing.txt"
            target.write_text("old", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("existing.txt", "new")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            target.write_text("changed by user", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                apply_import(zip_path, plan, project_root=root)
            self.assertEqual(target.read_text(
                encoding="utf-8"), "changed by user")

    def test_undo_restores_replaced_and_removes_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "existing.txt").write_text("old", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("existing.txt", "new")
                zf.writestr("new.txt", "created")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            applied, transaction_id = apply_import(
                zip_path, plan, project_root=root)
            self.assertEqual(applied, 2)
            self.assertIsNotNone(transaction_id)
            assert transaction_id is not None
            self.assertEqual(
                (root / "existing.txt").read_text(encoding="utf-8"), "new")
            assert transaction_id is not None
            restored = undo_import(transaction_id)
            self.assertEqual(restored, 2)
            self.assertEqual(
                (root / "existing.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((root / "new.txt").exists())
