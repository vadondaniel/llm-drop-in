from __future__ import annotations

import json

import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

import core.import_history as import_history_module
import core.importer as importer_module
from core.importer import (
    apply_import, cleanup_orphaned_transactions, enrich_plan_hashes, make_plan, undo_import,
)
from core.settings import TRANSACTION_DIR, remember_project
from core.zip_analyzer import (
    ProjectMatchHandling, ProjectMatchLevel, analyze, analyze_for_import,
    assess_project_match, is_plausibly_for_project, project_match_handling,
)


class ZipIgnoreTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "Git is required for ignore-rule tests")
    def test_truncated_archive_name_still_matches_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "very-long-llm-drop-in-project-name"
            root.mkdir()
            zip_path = Path(tmp) / "very-long-llm-drop-in-proj.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("src/feature.py", "changed")
            analysis = analyze_for_import(zip_path, root)
            self.assertTrue(analysis.archive_name_matches_project)

    def test_short_name_fragment_does_not_match_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "long-llm-drop-in-project"
            root.mkdir()
            zip_path = Path(tmp) / "long-llm.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("src/feature.py", "changed")
            analysis = analyze_for_import(zip_path, root)
            self.assertFalse(analysis.archive_name_matches_project)

    def test_replaced_suffix_after_truncated_project_name_still_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "very-long-llm-drop-in-project-structure-name"
            root.mkdir()
            zip_path = Path(tmp) / "very-long-llm-drop-in-fixed-final(2).zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("src/feature.py", "changed")
            analysis = analyze_for_import(zip_path, root)
            self.assertTrue(analysis.archive_name_matches_project)

    def test_unrelated_shared_two_token_prefix_does_not_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "very-long-llm-drop-in-project-structure-name"
            root.mkdir()
            zip_path = Path(tmp) / "very-long-llm-other-fix.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("src/feature.py", "changed")
            analysis = analyze_for_import(zip_path, root)
            self.assertFalse(analysis.archive_name_matches_project)

    def test_gitignored_files_are_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text(
                "build/\n*.log\n!important.log\n", encoding="utf-8"
            )
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("wrapper/src/main.py", "print('ok')")
                zf.writestr("wrapper/debug.log", "ignored")
                zf.writestr("wrapper/build/output.bin", b"ignored")
                zf.writestr("wrapper/important.log", "included")

            entries, candidates = analyze(zip_path, root)
            names = {e.archive_path for e in entries if not e.is_dir}
            self.assertEqual(
                names,
                {
                    "wrapper/src/main.py",
                    "wrapper/important.log",
                },
            )
            self.assertTrue(candidates)

            entries, _ = analyze(
                zip_path, root, include_gitignored=True
            )
            names = {e.archive_path for e in entries if not e.is_dir}
            self.assertEqual(
                names,
                {
                    "wrapper/src/main.py",
                    "wrapper/debug.log",
                    "wrapper/build/output.bin",
                    "wrapper/important.log",
                },
            )

    def test_make_plan_can_use_precomputed_hashes_without_zip_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "same.txt"
            target.write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("wrapper/same.txt", "same")

            analysis = analyze_for_import(zip_path, root)
            zip_path.unlink()
            plan = make_plan(
                analysis.filtered_entries,
                analysis.filtered_candidates[0].prefix,
                root,
                existing_files=analysis.existing_files,
                archive_hashes=analysis.archive_hashes,
            )

            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].action, "unchanged")
            self.assertIsNotNone(plan[0].new_hash)

    def test_project_match_accepts_existing_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "src" / "feature").mkdir(parents=True)
            (root / "src" / "feature" /
             "planner.py").write_text("one", encoding="utf-8")
            (root / "src" / "feature" / "reader.py").write_text("two", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("src/feature/planner.py", "changed")
                zf.writestr("src/feature/reader.py", "changed")
                zf.writestr("src/feature/new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(is_plausibly_for_project(analysis))

    def test_project_match_accepts_one_unchanged_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "src" / "feature").mkdir(parents=True)
            (root / "src" / "feature" /
             "marker.py").write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("src/feature/marker.py", "same")
                zf.writestr("src/feature/new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(is_plausibly_for_project(analysis))

    def test_project_match_rejects_generic_root_readme_even_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "README.md").write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "unrelated.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("README.md", "same")

            analysis = analyze_for_import(zip_path, root)

            self.assertFalse(is_plausibly_for_project(analysis))

    def test_project_match_rejects_generic_root_index_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "index.html").write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "unrelated.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("index.html", "changed")

            analysis = analyze_for_import(zip_path, root)

            self.assertFalse(is_plausibly_for_project(analysis))

    def test_project_match_accepts_single_distinctive_changed_nested_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            (root / "src" / "pagevoice" / "offline").mkdir(parents=True)
            target = root / "src" / "pagevoice" / "offline" / "DownloadPlanner.java"
            target.write_text("old", encoding="utf-8")
            zip_path = Path(tmp) / "partial.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr(
                    "project/src/pagevoice/offline/DownloadPlanner.java", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(is_plausibly_for_project(analysis))

    def test_project_match_rejects_two_generic_root_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "README.md").write_text("same", encoding="utf-8")
            (root / "index.html").write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "unrelated.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("README.md", "changed")
                zf.writestr("index.html", "changed")

            analysis = analyze_for_import(zip_path, root)

            self.assertFalse(is_plausibly_for_project(analysis))

    def test_project_match_classifies_generic_overlap_as_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "README.md").write_text("same", encoding="utf-8")
            (root / "index.html").write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "uncertain.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("README.md", "changed")
                zf.writestr("index.html", "changed")

            analysis = analyze_for_import(zip_path, root)

            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.UNCERTAIN,
            )

    def test_project_match_classifies_named_project_wrapper_as_likely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pagevoice"
            root.mkdir()
            zip_path = Path(tmp) / "named.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("pagevoice/new_file.py", "new")

            analysis = analyze_for_import(zip_path, root)

            assessment = assess_project_match(analysis)
            self.assertEqual(assessment.level, ProjectMatchLevel.LIKELY)
            self.assertTrue(is_plausibly_for_project(analysis))

    def test_project_match_filename_allows_partial_zip_with_descriptive_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "llm-dropin"
            root.mkdir()
            zip_path = Path(tmp) / \
                "llm-dropin-download-monitor-project-check2.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("brand_new_file.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(analysis.archive_name_matches_project)
            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.LIKELY,
            )

    def test_project_match_filename_ignores_duplicate_download_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "llm-dropin"
            root.mkdir()
            zip_path = Path(tmp) / "llm-dropin(12).zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("only_new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(analysis.archive_name_matches_project)
            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.LIKELY,
            )

    def test_project_match_filename_normalizes_case_spaces_and_punctuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LLM-Drop In"
            root.mkdir()
            zip_path = Path(tmp) / "llm_drop-in_tooltip-fix.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("only_new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(analysis.archive_name_matches_project)
            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.LIKELY,
            )

    def test_project_match_filename_treats_numeric_revision_as_project_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "llm-dropin"
            root.mkdir()
            zip_path = Path(tmp) / "llm-dropin2.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("only_new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(analysis.archive_name_matches_project)
            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.LIKELY,
            )

    def test_project_match_normalizes_project_folder_separators_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LLM Drop-In"
            root.mkdir()
            zip_path = Path(tmp) / "llm-dropin-tooltip-fix.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("only_new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertTrue(analysis.archive_name_matches_project)
            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.LIKELY,
            )

    def test_project_match_normalizes_named_wrapper_folder_separators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LLM Drop-In"
            root.mkdir()
            zip_path = Path(tmp) / "partial.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("llm-drop_in/only_new.py", "new")

            analysis = analyze_for_import(zip_path, root)

            self.assertEqual(
                assess_project_match(analysis).level,
                ProjectMatchLevel.LIKELY,
            )

    def test_project_match_policy_matches_manual_and_automatic_matrix(self):
        expected_manual = {
            ProjectMatchLevel.LIKELY: ProjectMatchHandling.ALLOW,
            ProjectMatchLevel.UNCERTAIN: ProjectMatchHandling.ALLOW,
            ProjectMatchLevel.UNLIKELY: ProjectMatchHandling.WARN,
        }
        expected_automatic = {
            ProjectMatchLevel.LIKELY: ProjectMatchHandling.ALLOW,
            ProjectMatchLevel.UNCERTAIN: ProjectMatchHandling.WARN,
            ProjectMatchLevel.UNLIKELY: ProjectMatchHandling.DISCARD,
        }

        for level, expected in expected_manual.items():
            self.assertEqual(
                project_match_handling(level, automatic=False), expected
            )
        for level, expected in expected_automatic.items():
            self.assertEqual(
                project_match_handling(level, automatic=True), expected
            )

    def test_project_match_rejects_archive_with_only_new_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "existing.txt").write_text("existing", encoding="utf-8")
            zip_path = Path(tmp) / "unrelated.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("other-project/main.py", "print('hello')")
                zf.writestr("other-project/config.json", "{}")

            analysis = analyze_for_import(zip_path, root)

            self.assertFalse(is_plausibly_for_project(analysis))


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

    def test_committed_import_can_recover_history_after_history_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "existing.txt"
            target.write_text("old", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("existing.txt", "new")

            tx_dir = Path(tmp) / "transactions"
            history_file = Path(tmp) / "history.json"
            app_dir = Path(tmp) / "app"
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file):
                applied, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                self.assertEqual(applied, 1)
                self.assertIsNotNone(transaction_id)
                assert transaction_id is not None
                recovered = import_history_module.reconcile_import_history()
                self.assertIn(transaction_id, recovered)
                history = import_history_module.load_history()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0].source_zip_name, "update.zip")
                self.assertEqual(history[0].project_path, str(root.resolve()))
                self.assertTrue(history[0].can_undo)

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
            manifest = json.loads(
                (TRANSACTION_DIR / transaction_id /
                 "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "undone")
            self.assertTrue(all(item["undone"] for item in manifest["files"]))

    def test_undo_resumes_after_a_partially_restored_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            old = root / "old.txt"
            new = root / "new.txt"
            old.write_text("old", encoding="utf-8")
            new.write_text("new", encoding="utf-8")

            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("old.txt", "newer")
                zf.writestr("new.txt", "created")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            applied, transaction_id = apply_import(
                zip_path, plan, project_root=root)
            self.assertEqual(applied, 2)
            assert transaction_id is not None

            # Simulate an interruption after the last file was restored.
            old.write_text("newer", encoding="utf-8")
            new.unlink()
            manifest_path = TRANSACTION_DIR / transaction_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][-1]["undone"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            restored = undo_import(transaction_id)
            self.assertEqual(restored, 1)
            self.assertEqual(old.read_text(encoding="utf-8"), "old")
            self.assertFalse(new.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "undone")
            self.assertTrue(all(item["undone"] for item in manifest["files"]))

    def test_partial_interrupted_commit_is_rolled_back_before_orphan_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "existing.txt"
            target.write_text("old", encoding="utf-8")
            tx_dir = Path(tmp) / "transactions" / "tx-partial"
            tx_dir.mkdir(parents=True)
            backup = tx_dir / "backup-00000000.txt.bak"
            backup.write_text("old", encoding="utf-8")
            target.write_text("new", encoding="utf-8")
            manifest = {
                "id": "tx-partial",
                "status": "committing",
                "files": [{
                    "target": str(target),
                    "backup": str(backup),
                    "staged": None,
                    "old_hash": importer_module._hash_file(backup),
                    "new_hash": importer_module._hash_file(target),
                    "commit_started": True,
                    "committed": True,
                    "undone": False,
                }],
            }
            (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(importer_module, "TRANSACTION_DIR", Path(tmp) / "transactions"):
                removed = cleanup_orphaned_transactions(set())

            self.assertEqual(removed, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertFalse(tx_dir.exists())

    def test_interrupted_commit_after_intent_but_before_replace_is_cleaned_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "existing.txt"
            target.write_text("old", encoding="utf-8")
            tx_dir = Path(tmp) / "transactions" / "tx-intent"
            tx_dir.mkdir(parents=True)
            backup = tx_dir / "backup-00000000.txt.bak"
            backup.write_text("old", encoding="utf-8")
            manifest = {
                "id": "tx-intent",
                "status": "committing",
                "files": [{
                    "target": str(target),
                    "backup": str(backup),
                    "staged": None,
                    "old_hash": importer_module._hash_file(target),
                    "new_hash": "does-not-match-current",
                    "commit_started": True,
                    "committed": False,
                    "undone": False,
                }],
            }
            (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(importer_module, "TRANSACTION_DIR", Path(tmp) / "transactions"):
                removed = cleanup_orphaned_transactions(set())

            self.assertEqual(removed, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertFalse(tx_dir.exists())

    def test_interrupted_commit_is_preserved_when_target_changed_after_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "existing.txt"
            target.write_text("user-change", encoding="utf-8")
            tx_dir = Path(tmp) / "transactions" / "tx-changed"
            tx_dir.mkdir(parents=True)
            backup = tx_dir / "backup-00000000.txt.bak"
            backup.write_text("old", encoding="utf-8")
            manifest = {
                "id": "tx-changed",
                "status": "committing",
                "files": [{
                    "target": str(target),
                    "backup": str(backup),
                    "staged": None,
                    "old_hash": importer_module._hash_file(backup),
                    "new_hash": "the-crashed-import",
                    "commit_started": True,
                    "committed": True,
                    "undone": False,
                }],
            }
            (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(importer_module, "TRANSACTION_DIR", Path(tmp) / "transactions"):
                removed = cleanup_orphaned_transactions(set())

            self.assertEqual(removed, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "user-change")
            self.assertTrue(tx_dir.exists())
            saved = json.loads(
                (tx_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "needs_manual_recovery")

    def test_interrupted_undo_survives_startup_cleanup_and_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            replaced = root / "replaced.txt"
            new_file = root / "new.txt"
            replaced.write_text("new", encoding="utf-8")
            # The new file was already restored/deleted before the simulated crash.

            tx_dir = Path(tmp) / "transactions" / "tx-undoing"
            tx_dir.mkdir(parents=True)
            backup = tx_dir / "backup.txt.bak"
            backup.write_text("old", encoding="utf-8")
            history_entry = import_history_module.HistoryEntry(**{
                "id": "history-undoing",
                "created_at": "2026-09-19T12:00:00+02:00",
                "project_key": str(root.resolve()).casefold(),
                "project_path": str(root.resolve()),
                "source_zip_path": str(Path(tmp) / "source.zip"),
                "source_zip_name": "source.zip",
                "file_count": 2,
                "new_count": 1,
                "replaced_count": 1,
                "unchanged_count": 0,
                "paths": ("replaced.txt", "new.txt"),
                "transaction_id": "tx-undoing",
                "state": import_history_module.STATE_APPLIED,
                "undo_state": import_history_module.UNDO_AVAILABLE,
            })
            manifest = {
                "id": "tx-undoing",
                "history": history_entry.to_dict(),
                "status": "undoing",
                "files": [
                    {
                        "target": str(replaced),
                        "backup": str(backup),
                        "staged": None,
                        "old_hash": importer_module._hash_file(backup),
                        "new_hash": importer_module._hash_file(replaced),
                        "commit_started": True,
                        "committed": True,
                        "undone": False,
                    },
                    {
                        "target": str(new_file),
                        "backup": None,
                        "staged": None,
                        "old_hash": None,
                        "new_hash": "hash-of-imported-new-file",
                        "commit_started": True,
                        "committed": True,
                        "undone": True,
                    },
                ],
            }
            (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            history_file = Path(tmp) / "history.json"
            app_dir = Path(tmp) / "app"
            tx_root = Path(tmp) / "transactions"
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_root), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_root), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file):
                import_history_module.save_history([history_entry])
                active = import_history_module.reconcile_import_history()
                self.assertIn("tx-undoing", active)
                self.assertEqual(cleanup_orphaned_transactions(active), 0)
                self.assertTrue(tx_dir.exists())

                restored = undo_import("tx-undoing")
                self.assertEqual(restored, 1)
                self.assertEqual(replaced.read_text(encoding="utf-8"), "old")
                self.assertFalse(new_file.exists())

    def test_manual_recovery_transaction_is_not_deleted_on_later_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "changed.txt"
            target.write_text("user-change", encoding="utf-8")
            tx_dir = Path(tmp) / "transactions" / "tx-manual"
            tx_dir.mkdir(parents=True)
            manifest = {
                "id": "tx-manual",
                "status": "needs_manual_recovery",
                "files": [],
            }
            (tx_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with mock.patch.object(importer_module, "TRANSACTION_DIR", Path(tmp) / "transactions"):
                self.assertEqual(cleanup_orphaned_transactions(set()), 0)
                self.assertTrue(tx_dir.exists())
                self.assertEqual(cleanup_orphaned_transactions(set()), 0)
                self.assertTrue(tx_dir.exists())

    def test_recent_projects_move_to_front_and_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            first.mkdir()
            second.mkdir()
            settings = {"project": "", "recent_projects": []}
            remember_project(settings, first)
            remember_project(settings, second)
            remember_project(settings, first)
            self.assertEqual(
                settings["recent_projects"],
                [str(first.resolve()), str(second.resolve())],
            )
            self.assertEqual(settings["project"], str(first.resolve()))

    def test_transaction_backup_cleanup_removes_unreferenced_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            existing = root / "existing.txt"
            existing.write_text("old", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("existing.txt", "new")
            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(
                zip_path, make_plan(entries, candidates[0].prefix, root)
            )
            applied, transaction_id = apply_import(
                zip_path, plan, project_root=root)
            self.assertEqual(applied, 1)
            assert transaction_id is not None
            tx_dir = TRANSACTION_DIR / transaction_id
            self.assertTrue(tx_dir.is_dir())
            backup_files = list(root.glob("*.llm-dropin-*.bak"))
            self.assertEqual(backup_files, [])
            backup_files = list(tx_dir.glob("*.bak"))
            self.assertEqual(len(backup_files), 1)

            from core.importer import cleanup_orphaned_transactions
            cleanup_orphaned_transactions(set())
            self.assertFalse(tx_dir.exists())
            self.assertFalse(backup_files[0].exists())


class ImportHistoryLifecycleTests(unittest.TestCase):
    def _history_paths(self, tmp: str):
        root = Path(tmp)
        tx_dir = root / "transactions"
        history_file = root / "history.json"
        app_dir = root / "app"
        return tx_dir, history_file, app_dir

    def test_apply_import_records_history_inside_core_transaction_boundary(self):
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
            tx_dir, history_file, app_dir = self._history_paths(tmp)
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file):
                applied, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                self.assertEqual(applied, 1)
                self.assertIsNotNone(transaction_id)
                history = import_history_module.load_history()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0].transaction_id, transaction_id)
                self.assertTrue(history[0].can_undo)

    def test_noop_import_recovery_manifest_reconstructs_history_after_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "same.txt"
            target.write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("same.txt", "same")

            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            tx_dir, history_file, app_dir = self._history_paths(tmp)
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file), \
                    mock.patch.object(importer_module, "record_applied_import", side_effect=OSError("history unavailable")):
                applied, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                self.assertEqual(applied, 0)
                self.assertIsNone(transaction_id)
                self.assertEqual(history_file.exists(), False)
                manifests = list(tx_dir.glob("*/manifest.json"))
                self.assertEqual(len(manifests), 1)

                recovered = import_history_module.reconcile_import_history()
                self.assertEqual(recovered, set())
                history = import_history_module.load_history()
                self.assertEqual(len(history), 1)
                self.assertTrue(history[0].is_noop)
                self.assertFalse(history[0].can_undo)

                cleanup_orphaned_transactions(recovered)
                self.assertFalse(manifests[0].parent.exists())

    def test_noop_import_is_retained_as_history_without_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "same.txt"
            target.write_text("same", encoding="utf-8")
            zip_path = Path(tmp) / "update.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("same.txt", "same")

            entries, candidates = analyze(zip_path, root)
            plan = enrich_plan_hashes(zip_path, make_plan(
                entries, candidates[0].prefix, root))
            tx_dir, history_file, app_dir = self._history_paths(tmp)
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file):
                applied, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                self.assertEqual(applied, 0)
                self.assertIsNone(transaction_id)
                history = import_history_module.load_history()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0].file_count, 1)
                self.assertEqual(history[0].unchanged_count, 1)
                self.assertFalse(history[0].can_undo)
                self.assertEqual(history[0].undo_state,
                                 import_history_module.UNDO_UNAVAILABLE)
                self.assertEqual(list(tx_dir.glob("*/manifest.json")), [])

    def test_undo_updates_history_from_core_transaction(self):
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
            tx_dir, history_file, app_dir = self._history_paths(tmp)
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file):
                _, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                assert transaction_id is not None
                restored = undo_import(transaction_id)
                self.assertEqual(restored, 1)
                entry = import_history_module.load_history()[0]
                self.assertEqual(
                    entry.state, import_history_module.STATE_UNDONE)
                self.assertFalse(entry.can_undo)

    def test_history_write_failure_after_commit_is_recovered_from_manifest(self):
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
            tx_dir, history_file, app_dir = self._history_paths(tmp)
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file), \
                    mock.patch.object(importer_module, "record_applied_import", side_effect=OSError("history unavailable")):
                applied, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                self.assertEqual(applied, 1)
                self.assertIsNotNone(transaction_id)
                self.assertFalse(history_file.exists())
                active = import_history_module.reconcile_import_history()
                self.assertEqual(active, {transaction_id})
                history = import_history_module.load_history()
                self.assertEqual(len(history), 1)
                self.assertTrue(history[0].can_undo)

    def test_failed_undo_is_recorded_as_failed_without_changing_applied_state(self):
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
            tx_dir, history_file, app_dir = self._history_paths(tmp)
            with mock.patch.object(importer_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "TRANSACTION_DIR", tx_dir), \
                    mock.patch.object(import_history_module, "APP_DIR", app_dir), \
                    mock.patch.object(import_history_module, "HISTORY_FILE", history_file):
                _, transaction_id = apply_import(
                    zip_path, plan, project_root=root)
                assert transaction_id is not None
                target.write_text("changed after import", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    undo_import(transaction_id)
                history = import_history_module.load_history()[0]
                self.assertEqual(
                    history.state, import_history_module.STATE_APPLIED)
                self.assertEqual(history.undo_state,
                                 import_history_module.UNDO_AVAILABLE)
