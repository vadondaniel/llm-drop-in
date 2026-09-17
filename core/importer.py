from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime

from .settings import TRANSACTION_DIR
from .zip_analyzer import ZipEntry, relative_after_prefix, validate_relative_path


@dataclass(frozen=True)
class ImportItem:
    archive_path: str
    target_path: Path
    action: str  # new / replace / unchanged / conflict
    size: int
    old_hash: str | None = None
    new_hash: str | None = None


def _hash_stream(stream, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def _hash_file(path: Path) -> str:
    with path.open("rb") as fh:
        return _hash_stream(fh)


def make_plan(
    entries: list[ZipEntry],
    prefix: str,
    project_root: Path,
    *,
    existing_files: dict[str, tuple[Path, str | None]] | None = None,
    archive_hashes: dict[str, str] | None = None,
) -> list[ImportItem]:
    root = project_root.resolve()
    plan: list[ImportItem] = []
    destinations: set[str] = set()
    cache = existing_files if existing_files is not None else {}

    for entry in entries:
        if entry.is_dir:
            continue
        rel = relative_after_prefix(entry.archive_path, prefix)
        if not rel:
            continue
        rel_path = validate_relative_path(rel)
        key = rel.replace("\\", "/").casefold()
        if key in destinations:
            raise ValueError(
                f"Multiple ZIP entries map to the same destination: {rel}")
        destinations.add(key)
        target = (root / Path(*rel_path.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Destination escapes project root: {rel}") from exc
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ValueError(f"Destination is not a regular file: {rel}")

        cached = cache.get(key)
        if cached is None:
            if target.is_file() and not target.is_symlink():
                cached = (target, _hash_file(target))
                cache[key] = cached
        elif cached[1] is None:
            cached = (cached[0], _hash_file(cached[0]))
            cache[key] = cached

        existing_path = cached[0] if cached is not None else None
        old_hash = cached[1] if cached is not None else None
        new_hash = archive_hashes.get(
            entry.archive_path) if archive_hashes else None
        if existing_path is None:
            action = "new"
        elif new_hash is not None and old_hash == new_hash:
            action = "unchanged"
        else:
            action = "replace"
        plan.append(
            ImportItem(
                entry.archive_path,
                target,
                action,
                entry.size,
                old_hash=old_hash,
                new_hash=new_hash,
            )
        )
    return plan


def enrich_plan_hashes(zip_path: Path, plan: list[ImportItem]) -> list[ImportItem]:
    with zipfile.ZipFile(zip_path) as zf:
        enriched: list[ImportItem] = []
        for item in plan:
            with zf.open(item.archive_path, "r") as src:
                new_hash = _hash_stream(src)
            action = item.action
            if item.old_hash and item.old_hash == new_hash:
                action = "unchanged"
            enriched.append(replace(item, action=action, new_hash=new_hash))
    return enriched


def unblock_file(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        os.remove(f"{path}:Zone.Identifier")
        return
    except FileNotFoundError:
        return
    except OSError:
        pass
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "Unblock-File -LiteralPath $args[0]", str(path)],
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _validate_current_state(effective_plan: list[ImportItem]) -> None:
    for item in effective_plan:
        target = item.target_path
        if item.old_hash is None:
            if target.exists():
                raise RuntimeError(
                    f"The destination appeared after analysis: {target}. Re-analyze the ZIP before importing."
                )
        else:
            if not target.exists() or target.is_symlink() or not target.is_file():
                raise RuntimeError(
                    f"The destination changed after analysis: {target}. Re-analyze the ZIP before importing."
                )
            current_hash = _hash_file(target)
            if current_hash != item.old_hash:
                raise RuntimeError(
                    f"The destination was modified after analysis: {target}. Re-analyze the ZIP before importing."
                )


def _transaction_dir(transaction_id: str) -> Path:
    return TRANSACTION_DIR / transaction_id


def _read_transaction_manifest(transaction_dir: Path) -> dict | None:
    manifest_path = transaction_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _cleanup_transaction_files(manifest: dict | None) -> None:
    if not manifest:
        return
    for item in manifest.get("files", []):
        for key in ("backup", "staged"):
            raw = item.get(key)
            if not raw:
                continue
            try:
                Path(raw).unlink(missing_ok=True)
            except OSError:
                pass


def cleanup_orphaned_transactions(active_ids: set[str] | None = None) -> int:
    """Remove transaction data no longer referenced by import history.

    Transaction backups are owned by the corresponding history entry. This
    keeps project-side .bak files from accumulating forever while preserving
    explicit undo for retained history entries.
    """
    active_ids = active_ids or set()
    if not TRANSACTION_DIR.is_dir():
        return 0
    removed = 0
    for transaction_dir in TRANSACTION_DIR.iterdir():
        if not transaction_dir.is_dir() or transaction_dir.name in active_ids:
            continue
        manifest = _read_transaction_manifest(transaction_dir)
        _cleanup_transaction_files(manifest)
        shutil.rmtree(transaction_dir, ignore_errors=True)
        removed += 1
    return removed


def apply_import(
    zip_path: Path,
    plan: list[ImportItem],
    prefix: str | None = None,
    project_root: Path | None = None,
) -> tuple[int, str | None]:
    """Apply an import and retain only the undo state needed by history."""
    effective_plan = [item for item in plan if item.action != "unchanged"]
    if not effective_plan:
        return 0, None

    _validate_current_state(effective_plan)
    TRANSACTION_DIR.mkdir(parents=True, exist_ok=True)
    transaction_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    transaction_dir = _transaction_dir(transaction_id)
    transaction_dir.mkdir()
    committed: list[tuple[Path, Path | None]] = []
    staged_files: list[Path] = []
    backup_files: list[Path] = []

    manifest = {
        "id": transaction_id,
        "zip": str(zip_path),
        "project": str(project_root.resolve()) if project_root else None,
        "files": [],
    }
    manifest_path = transaction_dir / "manifest.json"

    def save_manifest() -> None:
        manifest_path.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for index, item in enumerate(effective_plan):
                target = item.target_path
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.parent / (
                    f".{target.name}.llm-dropin-{transaction_id}-{index:08d}.tmp"
                )
                with zf.open(item.archive_path, "r") as src, staged.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                staged_files.append(staged)
                staged_hash = _hash_file(staged)
                if item.new_hash and staged_hash != item.new_hash:
                    raise ValueError(
                        f"Verification failed for {item.archive_path}")
                manifest["files"].append({
                    "target": str(target),
                    "backup": None,
                    "staged": str(staged),
                    "old_hash": item.old_hash,
                    "new_hash": item.new_hash,
                })
        save_manifest()

        _validate_current_state(effective_plan)

        for index, item in enumerate(effective_plan):
            target = item.target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                if not target.is_file() or target.is_symlink():
                    raise ValueError(
                        f"Refusing to replace non-regular file: {target}")
                backup = transaction_dir / \
                    f"backup-{index:08d}{target.suffix}.bak"
                backup_files.append(backup)
                shutil.copy2(target, backup)
            staged = Path(manifest["files"][index]["staged"])
            os.replace(staged, target)
            staged_files.remove(staged)
            unblock_file(target)
            committed.append((target, backup))
            manifest["files"][index]["backup"] = str(
                backup) if backup else None
            manifest["files"][index]["staged"] = None
            save_manifest()

        return len(effective_plan), transaction_id
    except Exception:
        for target, backup in reversed(committed):
            try:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
        _cleanup_transaction_files(manifest)
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise


def undo_import(transaction_id: str) -> int:
    transaction_dir = _transaction_dir(transaction_id)
    manifest = _read_transaction_manifest(transaction_dir)
    if manifest is None:
        raise ValueError("Import transaction no longer exists.")

    restored = 0
    files = manifest.get("files", [])
    for item in reversed(files):
        target = Path(item["target"])
        backup = Path(item["backup"]) if item.get("backup") else None
        current = (
            _hash_file(target)
            if target.exists() and target.is_file() and not target.is_symlink()
            else None
        )
        if current != item.get("new_hash"):
            raise RuntimeError(
                f"Cannot undo because the file changed after import: {target}"
            )
        if backup is not None and backup.exists():
            restore_tmp = target.parent / (
                f".{target.name}.llm-dropin-{transaction_id}-{files.index(item):08d}.tmp"
            )
            try:
                shutil.copy2(backup, restore_tmp)
                os.replace(restore_tmp, target)
            finally:
                restore_tmp.unlink(missing_ok=True)
        elif target.exists():
            target.unlink()
        restored += 1

    shutil.rmtree(transaction_dir, ignore_errors=True)
    return restored


def history_entry(zip_path: Path, project_root: Path, plan: list[ImportItem], *, success: bool = True, transaction_id: str | None = None) -> dict:
    return {
        "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "zip": str(zip_path),
        "project": str(project_root),
        "files": len(plan),
        "replaced": sum(1 for x in plan if x.action == "replace"),
        "new": sum(1 for x in plan if x.action == "new"),
        "unchanged": sum(1 for x in plan if x.action == "unchanged"),
        "paths": [str(x.target_path.relative_to(project_root)) for x in plan],
        "success": success,
        "transaction_id": transaction_id,
    }
