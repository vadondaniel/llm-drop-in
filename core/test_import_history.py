from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import core.import_history as history_module


class ImportHistoryTests(unittest.TestCase):
    def _patch_store(self, tmp: str):
        root = Path(tmp)
        return mock.patch.multiple(
            history_module,
            APP_DIR=root,
            HISTORY_FILE=root / "history.json",
            TRANSACTION_DIR=root / "transactions",
        )

    def _entry(self, project: Path, *, entry_id: str, tx: str | None = None):
        plan = [
            SimpleNamespace(action="replace",
                            target_path=project / "src" / "a.py"),
            SimpleNamespace(
                action="new", target_path=project / "src" / "b.py"),
            SimpleNamespace(action="unchanged",
                            target_path=project / "README.md"),
        ]
        return history_module.build_history_entry(
            Path("/tmp/source.zip"), project, plan, transaction_id=tx
        )

    def test_record_and_filter_history_by_normalized_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                other = Path(tmp) / "other"
                project.mkdir()
                other.mkdir()
                first = self._entry(project, entry_id="first")
                second = self._entry(other, entry_id="second")
                # Build IDs are UUIDs; replace the IDs and timestamps for deterministic assertions.
                first = history_module.HistoryEntry(**{
                    **first.__dict__,
                    "id": "first",
                    "created_at": "2026-09-19T12:00:02+02:00",
                })
                second = history_module.HistoryEntry(**{
                    **second.__dict__,
                    "id": "second",
                    "created_at": "2026-09-19T12:00:01+02:00",
                })
                history_module.record_applied_import(second)
                history_module.record_applied_import(first)

                loaded = history_module.load_history()
                self.assertEqual([entry.id for entry in loaded], [
                                 "first", "second"])
                self.assertEqual(
                    [entry.id for entry in history_module.get_history_for_project(
                        project)],
                    ["first"],
                )

    def test_history_order_is_newest_first_even_when_persisted_out_of_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                newest = self._entry(project, entry_id="newest")
                oldest = self._entry(project, entry_id="oldest")
                newest = history_module.HistoryEntry(**{
                    **newest.__dict__, "id": "newest", "created_at": "2026-09-19T12:00:02+02:00"
                })
                oldest = history_module.HistoryEntry(**{
                    **oldest.__dict__, "id": "oldest", "created_at": "2026-09-19T12:00:01+02:00"
                })
                history_module.save_history([oldest, newest])
                self.assertEqual([entry.id for entry in history_module.load_history()], [
                                 "newest", "oldest"])

    def test_structurally_invalid_primary_history_falls_back_to_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = self._entry(project, entry_id="backup-entry")
                entry = history_module.HistoryEntry(**{
                    **entry.__dict__, "id": "backup-entry"
                })
                history_module.save_history([entry])
                changed = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "backup-entry-2"})
                history_module.save_history([changed])
                history_module.HISTORY_FILE.write_text(
                    json.dumps({"not": "a history list"}), encoding="utf-8"
                )

                self.assertEqual(
                    [item.id for item in history_module.load_history()],
                    ["backup-entry"],
                )

    def test_history_order_is_deterministic_for_identical_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                first = self._entry(project, entry_id="a")
                second = self._entry(project, entry_id="b")
                first = history_module.HistoryEntry(**{
                    **first.__dict__, "id": "a", "created_at": "2026-09-19T12:00:00+02:00"
                })
                second = history_module.HistoryEntry(**{
                    **second.__dict__, "id": "b", "created_at": "2026-09-19T12:00:00+02:00"
                })
                history_module.save_history([first, second])

                self.assertEqual(
                    [entry.id for entry in history_module.load_history()],
                    ["b", "a"],
                )

    def test_has_history_entry_is_scoped_to_persisted_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = self._entry(project, entry_id="present")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "present"})
                history_module.save_history([entry])

                self.assertTrue(history_module.has_history_entry("present"))
                self.assertFalse(history_module.has_history_entry("missing"))

    def test_empty_entry_is_also_explicitly_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = history_module.build_history_entry(
                    Path(tmp) / "empty.zip", project, [])
                self.assertTrue(entry.is_noop)
                self.assertFalse(entry.can_undo)

    def test_noop_entry_is_explicitly_identified(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                plan = [
                    SimpleNamespace(action="unchanged",
                                    target_path=project / "README.md"),
                    SimpleNamespace(action="unchanged",
                                    target_path=project / "src" / "a.py"),
                ]
                entry = history_module.build_history_entry(
                    Path(tmp) / "same.zip", project, plan)
                self.assertTrue(entry.is_noop)
                self.assertFalse(entry.can_undo)

    def test_history_requires_current_timestamp_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = self._entry(project, entry_id="current")
                raw = entry.to_dict()
                self.assertIsNotNone(
                    history_module.HistoryEntry.from_dict(raw))

                legacy_key = {**raw, "created_at": None}
                self.assertIsNone(
                    history_module.HistoryEntry.from_dict(legacy_key))

                legacy_key = {**raw, "created_at": "2026-09-19T12:00:00"}
                self.assertIsNone(
                    history_module.HistoryEntry.from_dict(legacy_key))

                legacy_key = {
                    **raw, "created_at": "2026-09-19T12:00:00.123456+02:00"}
                self.assertIsNone(
                    history_module.HistoryEntry.from_dict(legacy_key))

                old_timestamp_key = {
                    **raw, "created_at": None, "timestamp": raw["created_at"]}
                self.assertIsNone(
                    history_module.HistoryEntry.from_dict(old_timestamp_key))

    def test_malformed_primary_history_recovers_from_atomic_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = history_module.HistoryEntry(
                    id="saved",
                    created_at="2026-09-19T12:00:00+02:00",
                    project_key=str(project.resolve()).casefold(),
                    project_path=str(project.resolve()),
                    source_zip_path=str(Path(tmp) / "update.zip"),
                    source_zip_name="update.zip",
                    file_count=1,
                    new_count=1,
                    replaced_count=0,
                    unchanged_count=0,
                    paths=("new.txt",),
                )
                history_module.save_history([entry])
                changed = history_module.HistoryEntry(
                    **{**entry.__dict__, "file_count": 2})
                history_module.save_history([changed])
                history_module.HISTORY_FILE.write_text(
                    "{truncated", encoding="utf-8")

                loaded = history_module.load_history()
                self.assertEqual([item.id for item in loaded], ["saved"])

    def test_failed_history_replacement_keeps_previous_recovery_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                original = self._entry(project, entry_id="original")
                original = history_module.HistoryEntry(**{
                    **original.__dict__, "id": "original", "created_at": "2026-09-19T12:00:01+02:00"
                })
                history_module.save_history([original])
                updated = history_module.HistoryEntry(**{
                    **original.__dict__, "id": "updated", "created_at": "2026-09-19T12:00:02+02:00"
                })

                with mock.patch.object(history_module.os, "replace", side_effect=OSError("simulated rename failure")):
                    with self.assertRaises(OSError):
                        history_module.save_history([updated])

                self.assertEqual(
                    [entry.id for entry in history_module.load_history()],
                    ["original"],
                )

    def test_retention_keeps_fifteen_newest_entries_per_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entries = []
                for index in range(35):
                    entry = self._entry(project, entry_id=f"{index:02d}")
                    entry = history_module.HistoryEntry(**{
                        **entry.__dict__,
                        "id": f"{index:02d}",
                        "created_at": f"2026-09-19T12:00:{index:02d}+02:00",
                    })
                    entries.append(entry)
                history_module.save_history(entries)

                loaded = history_module.load_history()
                self.assertEqual(len(loaded), 15)
                self.assertEqual(loaded[0].id, "34")
                self.assertEqual(loaded[-1].id, "20")

    def test_undo_transition_is_explicit_and_idempotently_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = self._entry(project, entry_id="entry", tx="tx")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "entry"})
                history_module.save_history([entry])

                updated = history_module.mark_undone("entry")
                if updated is None:
                    self.fail("mark_undone() returned None")
                self.assertEqual(updated.state, history_module.STATE_UNDONE)
                self.assertFalse(updated.can_undo)
                self.assertEqual(updated.transaction_id, "tx")

                updated_again = history_module.mark_undo_unavailable("entry")
                if updated_again is None:
                    self.fail("mark_undo_unavailable() returned None")
                self.assertEqual(updated_again.state,
                                 history_module.STATE_UNDONE)
                self.assertEqual(updated_again.undo_state,
                                 history_module.UNDO_UNAVAILABLE)

    def test_mark_undo_failed_preserves_applied_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = self._entry(project, entry_id="failed", tx="tx-failed")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "failed"})
                history_module.save_history([entry])

                updated = history_module.record_undo_failure("failed")
                if updated is None:
                    self.fail("record_undo_failure() returned None")
                self.assertEqual(updated.state, history_module.STATE_APPLIED)
                self.assertEqual(updated.undo_state,
                                 history_module.UNDO_FAILED)
                self.assertFalse(updated.can_undo)

    def test_reconcile_recovers_fully_committed_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                tx_dir = Path(tmp) / "transactions" / "tx-1"
                tx_dir.mkdir(parents=True)
                entry = self._entry(project, entry_id="recovered", tx="tx-1")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "recovered"})
                manifest = {
                    "id": "tx-1",
                    "history": entry.to_dict(),
                    "status": "committing",
                    "files": [
                        {"target": str(project / "a.txt"),
                         "committed": True, "new_hash": "hash"}
                    ],
                }
                (tx_dir / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )

                active = history_module.reconcile_import_history()
                self.assertIn("tx-1", active)
                self.assertEqual(history_module.load_history()
                                 [0].id, "recovered")

    def test_reconcile_recovers_undo_finished_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                tx_dir = Path(tmp) / "transactions" / "tx-undone"
                tx_dir.mkdir(parents=True)
                entry = self._entry(
                    project, entry_id="undo-finished", tx="tx-undone")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "undo-finished"})
                history_module.save_history([entry])
                manifest = {
                    "id": "tx-undone",
                    "history": entry.to_dict(),
                    "status": "undone",
                    "files": [
                        {"target": str(project / "a.txt"),
                         "committed": True, "undone": True},
                    ],
                }
                (tx_dir / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )

                active = history_module.reconcile_import_history()
                self.assertNotIn("tx-undone", active)
                updated = history_module.load_history()[0]
                self.assertEqual(updated.state, history_module.STATE_UNDONE)
                self.assertEqual(updated.undo_state,
                                 history_module.UNDO_UNAVAILABLE)
                self.assertFalse(updated.can_undo)

    def test_reconcile_marks_missing_undo_transaction_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                entry = self._entry(project, entry_id="missing", tx="gone")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "missing"})
                history_module.save_history([entry])

                active = history_module.reconcile_import_history()
                self.assertNotIn("gone", active)
                updated = history_module.load_history()[0]
                self.assertEqual(updated.undo_state,
                                 history_module.UNDO_UNAVAILABLE)
                self.assertFalse(updated.can_undo)

    def test_retention_prunes_undo_transactions_per_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project_a = Path(tmp) / "project-a"
                project_b = Path(tmp) / "project-b"
                project_a.mkdir()
                project_b.mkdir()
                tx_dir = Path(tmp) / "transactions"
                tx_dir.mkdir()
                entries = []
                for prefix, project in (("a", project_a), ("b", project_b)):
                    for index in range(history_module.DEFAULT_HISTORY_LIMIT + 1):
                        tx_id = f"tx-{prefix}-{index}"
                        tx_path = tx_dir / tx_id
                        tx_path.mkdir()
                        (tx_path / "manifest.json").write_text(
                            json.dumps({"files": []}), encoding="utf-8")
                        entry = self._entry(
                            project, entry_id=f"{prefix}-{index:02d}", tx=tx_id)
                        entry = history_module.HistoryEntry(**{
                            **entry.__dict__,
                            "id": f"{prefix}-{index:02d}",
                            "created_at": f"2026-09-19T12:00:{index:02d}+02:00",
                        })
                        entries.append(entry)
                history_module.save_history(entries)

                self.assertEqual(
                    [entry.id for entry in history_module.get_history_for_project(
                        project_a)],
                    [f"a-{index:02d}" for index in range(
                        history_module.DEFAULT_HISTORY_LIMIT, 0, -1)],
                )
                self.assertEqual(
                    [entry.id for entry in history_module.get_history_for_project(
                        project_b)],
                    [f"b-{index:02d}" for index in range(
                        history_module.DEFAULT_HISTORY_LIMIT, 0, -1)],
                )
                self.assertFalse((tx_dir / "tx-a-0").exists())
                self.assertFalse((tx_dir / "tx-b-0").exists())
                self.assertTrue(
                    (tx_dir / f"tx-a-{history_module.DEFAULT_HISTORY_LIMIT}").exists())
                self.assertTrue(
                    (tx_dir / f"tx-b-{history_module.DEFAULT_HISTORY_LIMIT}").exists())

    def test_reconcile_can_recover_when_history_file_is_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_store(tmp):
                project = Path(tmp) / "project"
                project.mkdir()
                tx_dir = Path(tmp) / "transactions" / "tx-recover"
                tx_dir.mkdir(parents=True)
                entry = self._entry(
                    project, entry_id="recover", tx="tx-recover")
                entry = history_module.HistoryEntry(
                    **{**entry.__dict__, "id": "recover"})
                (tx_dir / "manifest.json").write_text(json.dumps({
                    "id": "tx-recover",
                    "history": entry.to_dict(),
                    "status": "committed",
                    "files": [{"committed": True, "undone": False}],
                }), encoding="utf-8")
                history_module.HISTORY_FILE.parent.mkdir(
                    parents=True, exist_ok=True)
                history_module.HISTORY_FILE.write_text(
                    "{broken", encoding="utf-8")

                active = history_module.reconcile_import_history()
                self.assertEqual(active, {"tx-recover"})
                self.assertEqual(history_module.load_history()
                                 [0].id, "recover")
