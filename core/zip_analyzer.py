from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from pathlib import Path
import zipfile


@dataclass(frozen=True)
class ZipEntry:
    archive_path: str
    is_dir: bool
    size: int


@dataclass(frozen=True)
class ZipCandidate:
    prefix: str
    mapped_count: int
    new_count: int
    score: float


def _clean_archive_path(name: str) -> str:
    name = name.replace("\\", "/").strip("/")
    parts = []
    for part in PurePosixPath(name).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Unsafe archive path: {name}")
        parts.append(part)
    return "/".join(parts)


def read_zip(path: Path) -> list[ZipEntry]:
    with zipfile.ZipFile(path) as zf:
        result = []
        for info in zf.infolist():
            clean = _clean_archive_path(info.filename)
            is_dir = info.is_dir() or info.filename.endswith(("/", "\\"))
            if not clean and not is_dir:
                raise ValueError("ZIP contains an invalid empty file path")
            result.append(ZipEntry(clean, is_dir, info.file_size))
        return result


def candidate_prefixes(entries: list[ZipEntry]) -> list[str]:
    files = [e.archive_path for e in entries if not e.is_dir]
    prefixes = {""}
    for file_path in files:
        parts = file_path.split("/")
        for i in range(1, len(parts)):
            prefixes.add("/".join(parts[:i]) + "/")
    return sorted(prefixes, key=lambda x: (x.count("/"), len(x)))


def relative_after_prefix(path: str, prefix: str) -> str | None:
    if prefix and not path.startswith(prefix):
        return None
    rel = path[len(prefix):] if prefix else path
    return rel or None


def analyze(zip_path: Path, project_root: Path) -> tuple[list[ZipEntry], list[ZipCandidate]]:
    entries = read_zip(zip_path)
    existing = {p.relative_to(project_root).as_posix()
                for p in project_root.rglob("*") if p.is_file()}
    candidates: list[ZipCandidate] = []

    for prefix in candidate_prefixes(entries):
        paths = []
        unsafe = False
        for e in entries:
            if e.is_dir:
                continue
            rel = relative_after_prefix(e.archive_path, prefix)
            if not rel:
                continue
            try:
                rel_parts = PurePosixPath(rel).parts
                if any(part in ("", ".", "..") for part in rel_parts):
                    unsafe = True
                    break
            except Exception:
                unsafe = True
                break
            paths.append(rel)
        if unsafe or not paths:
            continue
        mapped = sum(1 for p in paths if p in existing)
        new = len(paths) - mapped
        # Prefer mappings that explain more files, then prefixes that are shorter.
        score = (mapped * 8 + new * 1) / max(1, len(paths))
        candidates.append(ZipCandidate(prefix, mapped, new, score))

    # Prefer the deepest wrapper that accounts for the same complete set of files.
    # This turns ZIPs such as project/project/src/... into a clean project-root match.
    candidates.sort(key=lambda c: (c.mapped_count, -c.new_count,
                    c.score, c.prefix.count("/"), -len(c.prefix)), reverse=True)
    return entries, candidates
