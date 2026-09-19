from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid
import shutil

from .settings import APP_DIR, TRANSACTION_DIR

HISTORY_FILE = APP_DIR / "history.json"


HISTORY_SCHEMA_VERSION = 1
DEFAULT_HISTORY_LIMIT = 15

STATE_APPLIED = "APPLIED"
STATE_UNDONE = "UNDONE"

UNDO_AVAILABLE = "AVAILABLE"
UNDO_UNAVAILABLE = "UNAVAILABLE"
UNDO_FAILED = "FAILED"


def _transaction_path(transaction_id: str | None) -> Path | None:
    if not transaction_id or not isinstance(transaction_id, str):
        return None
    return TRANSACTION_DIR / transaction_id


def _discard_transaction(transaction_id: str | None) -> None:
    path = _transaction_path(transaction_id)
    if path is None or not path.is_dir():
        return
    try:
        manifest_path = path / "manifest.json"
        raw = _read_json(manifest_path)
        if isinstance(raw, dict):
            for item in raw.get("files", []):
                if not isinstance(item, dict):
                    continue
                for key in ("backup", "staged"):
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        Path(value).unlink(missing_ok=True)
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=True)


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    created_at: str
    project_key: str
    project_path: str
    source_zip_path: str
    source_zip_name: str
    file_count: int
    new_count: int
    replaced_count: int
    unchanged_count: int
    paths: tuple[str, ...] = field(default_factory=tuple)
    transaction_id: str | None = None
    state: str = STATE_APPLIED
    undo_state: str = UNDO_UNAVAILABLE
    schema_version: int = HISTORY_SCHEMA_VERSION

    @property
    def is_undone(self) -> bool:
        return self.state == STATE_UNDONE

    @property
    def is_noop(self) -> bool:
        """Whether the recorded import produced no file changes."""
        return (
            self.new_count == 0
            and self.replaced_count == 0
            and self.unchanged_count == self.file_count
        )

    @property
    def can_undo(self) -> bool:
        return (
            self.state == STATE_APPLIED
            and self.undo_state == UNDO_AVAILABLE
            and bool(self.transaction_id)
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "created_at": self.created_at,
            "project_key": self.project_key,
            "project_path": self.project_path,
            "source_zip_path": self.source_zip_path,
            "source_zip_name": self.source_zip_name,
            "file_count": self.file_count,
            "new_count": self.new_count,
            "replaced_count": self.replaced_count,
            "unchanged_count": self.unchanged_count,
            "paths": list(self.paths),
            "transaction_id": self.transaction_id,
            "state": self.state,
            "undo_state": self.undo_state,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "HistoryEntry | None":
        if not isinstance(raw, dict):
            return None
        try:
            project_path = _clean_path(
                raw.get("project_path", raw.get("project")))
            if not project_path:
                return None
            source_zip_path = _clean_path(
                raw.get("source_zip_path", raw.get("zip")), allow_empty=True
            )
            source_zip_name = raw.get("source_zip_name")
            if not isinstance(source_zip_name, str) or not source_zip_name:
                source_zip_name = Path(
                    source_zip_path).name if source_zip_path else "Unknown ZIP"

            entry_id = raw.get("id")
            if not isinstance(entry_id, str) or not entry_id:
                return None
            created_at = raw.get("created_at")
            if not isinstance(created_at, str) or not created_at:
                return None
            timestamp = datetime.fromisoformat(created_at)
            if timestamp.tzinfo is None or timestamp.microsecond:
                return None

            paths = raw.get("paths", [])
            if not isinstance(paths, list):
                paths = []
            paths = tuple(value for value in paths if isinstance(value, str))

            tx_id = raw.get("transaction_id")
            if not isinstance(tx_id, str) or not tx_id:
                tx_id = None

            state = raw.get("state")
            if state not in {STATE_APPLIED, STATE_UNDONE}:
                state = STATE_UNDONE if raw.get("undone") else STATE_APPLIED

            undo_state = raw.get("undo_state")
            if undo_state not in {UNDO_AVAILABLE, UNDO_UNAVAILABLE, UNDO_FAILED}:
                undo_state = UNDO_AVAILABLE if tx_id and state == STATE_APPLIED else UNDO_UNAVAILABLE

            if state == STATE_UNDONE:
                undo_state = UNDO_UNAVAILABLE

            return cls(
                id=entry_id,
                created_at=created_at,
                project_key=_project_key(Path(project_path)),
                project_path=project_path,
                source_zip_path=source_zip_path,
                source_zip_name=source_zip_name,
                file_count=_nonnegative_int(
                    raw.get("file_count", raw.get("files", 0))),
                new_count=_nonnegative_int(
                    raw.get("new_count", raw.get("new", 0))),
                replaced_count=_nonnegative_int(
                    raw.get("replaced_count", raw.get("replaced", 0))),
                unchanged_count=_nonnegative_int(
                    raw.get("unchanged_count", raw.get("unchanged", 0))),
                paths=paths,
                transaction_id=tx_id,
                state=state,
                undo_state=undo_state,
                schema_version=HISTORY_SCHEMA_VERSION,
            )
        except (OSError, TypeError, ValueError):
            return None


def _nonnegative_int(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _clean_path(value, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        return "" if allow_empty else ""
    value = value.strip()
    if not value and not allow_empty:
        return ""
    try:
        return str(Path(value).resolve()) if value else ""
    except OSError:
        return value


def _project_key(project_path: Path) -> str:
    return str(project_path.resolve()).casefold()


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _valid_history_payload(payload: object) -> list:
    if not isinstance(payload, list):
        return []
    return payload


def _load_raw_history() -> list:
    payload = _read_json(HISTORY_FILE)
    if isinstance(payload, list):
        return payload

    # A syntactically valid but structurally invalid primary file is still
    # corrupt for our purposes; fall back to the last known-good backup.
    backup = HISTORY_FILE.with_suffix(HISTORY_FILE.suffix + ".bak")
    payload = _read_json(backup)
    return _valid_history_payload(payload)


def _atomic_write(payload: list) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f"{HISTORY_FILE.name}.", suffix=".tmp", dir=APP_DIR)
    tmp_path = Path(raw_tmp)
    backup = HISTORY_FILE.with_suffix(HISTORY_FILE.suffix + ".bak")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())

        # Preserve the last known-good history file before replacing it. Do
        # not overwrite an existing valid backup with a corrupt primary file;
        # that backup is the recovery path when a previous write was cut short.
        primary_payload = _read_json(
            HISTORY_FILE) if HISTORY_FILE.is_file() else None
        if primary_payload is not None:
            try:
                shutil.copy2(HISTORY_FILE, backup)
            except OSError:
                pass

        os.replace(tmp_path, HISTORY_FILE)
    finally:
        tmp_path.unlink(missing_ok=True)


def _sort_history(entries: list[HistoryEntry]) -> list[HistoryEntry]:
    # Timestamps are the semantic ordering; list position is only the on-disk
    # representation.
    return sorted(entries, key=lambda entry: (_created_at_key(entry), entry.id), reverse=True)


def load_history() -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    seen_ids: set[str] = set()
    for raw in _load_raw_history():
        entry = HistoryEntry.from_dict(raw)
        if entry is None or entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        entries.append(entry)
    return _sort_history(entries)


def save_history(entries: list[HistoryEntry]) -> list[HistoryEntry]:
    deduped: list[HistoryEntry] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        deduped.append(entry)
    deduped = _sort_history(deduped)

    # Retention is project-scoped. One project's imports must never evict
    # another project's history.
    kept: list[HistoryEntry] = []
    counts_by_project: dict[str, int] = {}
    for entry in deduped:
        count = counts_by_project.get(entry.project_key, 0)
        if count < DEFAULT_HISTORY_LIMIT:
            kept.append(entry)
            counts_by_project[entry.project_key] = count + 1

    kept_ids = {entry.id for entry in kept}
    pruned = [entry for entry in deduped if entry.id not in kept_ids]
    _atomic_write([entry.to_dict() for entry in kept])
    for entry in pruned:
        _discard_transaction(entry.transaction_id)
    return kept


def record_applied_import(entry: HistoryEntry) -> HistoryEntry:
    """Persist a completed import as the canonical history operation."""
    return _record_import(entry)


def record_undo_failure(history_id: str) -> HistoryEntry | None:
    return mark_undo_unavailable(history_id, failed=True)


def reconcile_import_history() -> set[str]:
    return _reconcile_transactions()


def _record_import(entry: HistoryEntry) -> HistoryEntry:
    history = load_history()
    history.append(entry)
    save_history(history)
    return entry


def mark_undone(history_id: str) -> HistoryEntry | None:
    history = load_history()
    updated: list[HistoryEntry] = []
    found: HistoryEntry | None = None
    for entry in history:
        if entry.id == history_id:
            found = HistoryEntry(**{
                **entry.__dict__,
                "state": STATE_UNDONE,
                "undo_state": UNDO_UNAVAILABLE,
            })
            updated.append(found)
        else:
            updated.append(entry)
    if found is None:
        return None
    save_history(updated)
    return found


def remove_history_entry(history_id: str) -> bool:
    """Remove one history entry and discard any retained undo transaction."""
    history = load_history()
    target = next((entry for entry in history if entry.id == history_id), None)
    if target is None:
        return False

    updated = [entry for entry in history if entry.id != history_id]
    save_history(updated)
    _discard_transaction(target.transaction_id)
    return True


def mark_undo_unavailable(history_id: str, *, failed: bool = False) -> HistoryEntry | None:
    history = load_history()
    updated: list[HistoryEntry] = []
    found = None
    for entry in history:
        if entry.id == history_id:
            found = HistoryEntry(**{
                **entry.__dict__,
                "undo_state": UNDO_FAILED if failed else UNDO_UNAVAILABLE,
            })
            updated.append(found)
        else:
            updated.append(entry)
    if found is None:
        return None
    save_history(updated)
    return found


def has_history_entry(history_id: str) -> bool:
    return any(entry.id == history_id for entry in load_history())


def get_history_for_project(project: Path, *, limit: int | None = None) -> list[HistoryEntry]:
    key = _project_key(project)
    result = [entry for entry in load_history() if entry.project_key == key]
    return result if limit is None else result[:max(0, limit)]


def build_history_entry(
    zip_path: Path,
    project_root: Path,
    plan,
    *,
    transaction_id: str | None = None,
) -> HistoryEntry:
    project = project_root.resolve()
    zip_resolved = zip_path.resolve()
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return HistoryEntry(
        id=uuid.uuid4().hex,
        created_at=created_at,
        project_key=_project_key(project),
        project_path=str(project),
        source_zip_path=str(zip_resolved),
        source_zip_name=zip_resolved.name,
        file_count=len(plan),
        new_count=sum(1 for item in plan if item.action == "new"),
        replaced_count=sum(1 for item in plan if item.action == "replace"),
        unchanged_count=sum(1 for item in plan if item.action == "unchanged"),
        paths=tuple(str(item.target_path.resolve().relative_to(project))
                    for item in plan),
        transaction_id=transaction_id,
        state=STATE_APPLIED,
        undo_state=UNDO_AVAILABLE if transaction_id else UNDO_UNAVAILABLE,
    )


def _read_manifest(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _created_at_key(entry: HistoryEntry):
    return datetime.fromisoformat(entry.created_at).timestamp()


def _recoverable_transaction(manifest: dict) -> bool:
    files = manifest.get("files")
    if not isinstance(files, list):
        return False
    status = manifest.get("status")
    if status == "history_only":
        return True
    if not files:
        return False
    if status == "undone":
        return all(isinstance(item, dict) and item.get("undone") is True for item in files)
    if status == "undoing":
        return all(isinstance(item, dict) and item.get("committed") is True for item in files)
    return all(isinstance(item, dict) and item.get("committed") is True for item in files)


def _reconcile_transactions() -> set[str]:
    """Recover history state from transaction manifests after interruption."""
    history = load_history()
    by_tx = {entry.transaction_id: entry for entry in history if entry.transaction_id}
    recovered = list(history)
    changed = False

    if TRANSACTION_DIR.is_dir():
        for transaction_dir in TRANSACTION_DIR.iterdir():
            if not transaction_dir.is_dir():
                continue
            manifest = _read_manifest(transaction_dir / "manifest.json")
            if not manifest or not _recoverable_transaction(manifest):
                continue
            tx_id = manifest.get("id") or transaction_dir.name
            if not isinstance(tx_id, str) or not tx_id:
                continue
            raw_history = manifest.get("history")
            entry = HistoryEntry.from_dict(raw_history) if isinstance(
                raw_history, dict) else None
            if entry is None or entry.transaction_id != tx_id:
                continue
            if manifest.get("status") == "undone":
                entry = HistoryEntry(**{
                    **entry.__dict__,
                    "state": STATE_UNDONE,
                    "undo_state": UNDO_UNAVAILABLE,
                })
            if tx_id in by_tx:
                old_entry = by_tx[tx_id]
                if old_entry != entry:
                    recovered = [entry if item.transaction_id ==
                                 tx_id else item for item in recovered]
                    by_tx[tx_id] = entry
                    changed = True
            else:
                recovered.insert(0, entry)
                by_tx[tx_id] = entry
                changed = True

    updated: list[HistoryEntry] = []
    for entry in recovered:
        if (
            entry.state == STATE_APPLIED
            and entry.undo_state == UNDO_AVAILABLE
            and entry.transaction_id
            and not (TRANSACTION_DIR / entry.transaction_id).is_dir()
        ):
            entry = HistoryEntry(**{
                **entry.__dict__,
                "undo_state": UNDO_UNAVAILABLE,
            })
            changed = True
        updated.append(entry)

    if changed:
        save_history(updated)

    return {
        entry.transaction_id
        for entry in load_history()
        if entry.transaction_id
        and entry.state == STATE_APPLIED
        and entry.undo_state == UNDO_AVAILABLE
    }
