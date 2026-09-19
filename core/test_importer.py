from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

from core.importer import apply_import, enrich_plan_hashes, make_plan, undo_import
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
