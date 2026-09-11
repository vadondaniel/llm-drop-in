from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime

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


def make_plan(entries: list[ZipEntry], prefix: str, project_root: Path) -> list[ImportItem]:
    root = project_root.resolve()
    plan: list[ImportItem] = []
    destinations: set[str] = set()
    with_project_paths = {p.relative_to(root).as_posix(
    ).casefold(): p for p in root.rglob("*") if p.is_file()}

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
        if target.exists() and not target.is_file():
            raise ValueError(f"Destination is not a regular file: {rel}")
        existing = with_project_paths.get(key)
        old_hash = _hash_file(existing) if existing else None
        action = "replace" if existing else "new"
        plan.append(ImportItem(entry.archive_path, target,
                    action, entry.size, old_hash=old_hash))
    return plan


def enrich_plan_hashes(zip_path: Path, plan: list[ImportItem]) -> list[ImportItem]:
    by_archive = {item.archive_path: item for item in plan}
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


def apply_import(zip_path: Path, plan: list[ImportItem], prefix: str | None = None) -> int:
    """Apply an import transactionally, with rollback if commit fails.

    Files are first fully extracted into a staging directory. Existing files are copied
    to backup files before replacement. If any commit operation fails, already-committed
    paths are restored from the backups and newly-created files are removed.
    """
    effective_plan = [item for item in plan if item.action != "unchanged"]
    if not effective_plan:
        return 0

    transaction_dir = Path(tempfile.mkdtemp(prefix="llm-dropin-"))
    staging_dir = transaction_dir / "staged"
    backup_dir = transaction_dir / "backups"
    staging_dir.mkdir()
    backup_dir.mkdir()
    committed: list[tuple[Path, Path | None]] = []

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for index, item in enumerate(effective_plan):
                staged = staging_dir / f"{index:08d}.file"
                with zf.open(item.archive_path, "r") as src, staged.open("wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                staged_hash = _hash_file(staged)
                if item.new_hash and staged_hash != item.new_hash:
                    raise ValueError(
                        f"Verification failed for {item.archive_path}")

        for index, item in enumerate(effective_plan):
            target = item.target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                if not target.is_file() or target.is_symlink():
                    raise ValueError(
                        f"Refusing to replace non-regular file: {target}")
                backup = backup_dir / f"{index:08d}.bak"
                shutil.copy2(target, backup)
            staged = staging_dir / f"{index:08d}.file"
            os.replace(staged, target)
            unblock_file(target)
            committed.append((target, backup))

        return len(effective_plan)
    except Exception:
        for target, backup in reversed(committed):
            try:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)


def history_entry(zip_path: Path, project_root: Path, plan: list[ImportItem], *, success: bool = True) -> dict:
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
    }
