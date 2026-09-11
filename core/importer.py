from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import subprocess
import zipfile
from datetime import datetime

from .zip_analyzer import ZipEntry, relative_after_prefix


@dataclass(frozen=True)
class ImportItem:
    archive_path: str
    target_path: Path
    action: str  # replace / new
    size: int


def make_plan(entries: list[ZipEntry], prefix: str, project_root: Path) -> list[ImportItem]:
    plan = []
    for entry in entries:
        if entry.is_dir:
            continue
        rel = relative_after_prefix(entry.archive_path, prefix)
        if not rel:
            continue
        rel_path = PurePosixPath(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise ValueError(f"Unsafe destination path: {rel}")
        target = (project_root / Path(*rel_path.parts)).resolve()
        root = project_root.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Destination escapes project root: {rel}") from exc
        plan.append(ImportItem(entry.archive_path, target,
                    "replace" if target.exists() else "new", entry.size))
    return plan


def unblock_file(path: Path) -> None:
    if os.name != "nt":
        return
    # Zone.Identifier is an NTFS alternate data stream. Delete it directly first.
    try:
        os.remove(f"{path}:Zone.Identifier")
        return
    except FileNotFoundError:
        return
    except OSError:
        pass

    # Fallback for filesystems/edge cases where direct ADS removal fails.
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


def apply_import(zip_path: Path, plan: list[ImportItem], prefix: str) -> int:
    # Files are intentionally written directly. Git provides the user's normal rollback/diff workflow.
    with zipfile.ZipFile(zip_path) as zf:
        for item in plan:
            item.target_path.parent.mkdir(parents=True, exist_ok=True)
            temp = item.target_path.with_name(
                item.target_path.name + ".llm-dropin.tmp")
            try:
                with zf.open(item.archive_path, "r") as src, open(temp, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                os.replace(temp, item.target_path)
                unblock_file(item.target_path)
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
    return len(plan)


def history_entry(zip_path: Path, project_root: Path, plan: list[ImportItem]) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "zip": str(zip_path),
        "project": str(project_root),
        "files": len(plan),
        "replaced": sum(1 for x in plan if x.action == "replace"),
        "new": sum(1 for x in plan if x.action == "new"),
        "paths": [str(x.target_path.relative_to(project_root)) for x in plan],
    }
