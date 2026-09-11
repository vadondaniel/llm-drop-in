from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import os
import zipfile


DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 1000.0


@dataclass(frozen=True)
class ZipEntry:
    archive_path: str
    is_dir: bool
    size: int
    compressed_size: int
    crc: int


@dataclass(frozen=True)
class ZipCandidate:
    prefix: str
    mapped_count: int
    new_count: int
    unchanged_count: int
    conflict_count: int
    considered_count: int
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


def _is_windows_reserved(part: str) -> bool:
    stem = part.rstrip(" .").split(".", 1)[0].upper()
    return stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_relative_path(rel: str) -> PurePosixPath:
    path = PurePosixPath(rel)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"Unsafe relative path: {rel}")
    if os.name == "nt":
        for part in path.parts:
            if any(ch in part for ch in '<>:\"|?*'):
                raise ValueError(f"Invalid Windows path component: {rel}")
            if _is_windows_reserved(part):
                raise ValueError(f"Reserved Windows path component: {rel}")
    return path


def read_zip(
    path: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> list[ZipEntry]:
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ValueError(
                f"ZIP contains {len(infos):,} entries; limit is {max_files:,}.")
        total_size = sum(info.file_size for info in infos)
        if total_size > max_uncompressed_bytes:
            raise ValueError(
                f"ZIP contains {total_size:,} bytes of uncompressed data; "
                f"limit is {max_uncompressed_bytes:,}."
            )
        result: list[ZipEntry] = []
        seen: set[str] = set()
        for info in infos:
            clean = _clean_archive_path(info.filename)
            is_dir = info.is_dir() or info.filename.endswith(("/", "\\"))
            if not clean and not is_dir:
                raise ValueError("ZIP contains an invalid empty file path")
            if not clean:
                continue
            if not is_dir:
                validate_relative_path(clean)
                if clean.casefold() in {p.casefold() for p in seen}:
                    raise ValueError(
                        f"ZIP contains duplicate/case-colliding path: {clean}")
                seen.add(clean)
                ratio = info.file_size / max(1, info.compress_size)
                if ratio > max_compression_ratio and info.file_size > 1024 * 1024:
                    raise ValueError(
                        f"ZIP entry has an unsafe compression ratio: {clean}")
            result.append(ZipEntry(clean, is_dir, info.file_size,
                          info.compress_size, info.CRC))
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


def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_files(project_root: Path) -> dict[str, tuple[Path, str]]:
    root = project_root.resolve()
    result: dict[str, tuple[Path, str]] = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(root).as_posix()
            result[rel.casefold()] = (p, _hash_file(p))
    return result


def analyze(zip_path: Path, project_root: Path) -> tuple[list[ZipEntry], list[ZipCandidate]]:
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise ValueError(
            "Project folder does not exist or is not a directory.")
    entries = read_zip(zip_path)
    existing = _existing_files(project_root)
    candidates: list[ZipCandidate] = []
    with zipfile.ZipFile(zip_path) as zf:
        archive_hashes: dict[str, str] = {}
        for e in entries:
            if e.is_dir:
                continue
            with zf.open(e.archive_path, "r") as src:
                digest = hashlib.sha256()
                while chunk := src.read(1024 * 1024):
                    digest.update(chunk)
                archive_hashes[e.archive_path] = digest.hexdigest()

    for prefix in candidate_prefixes(entries):
        mapped = unchanged = new = conflicts = 0
        paths: list[str] = []
        seen_targets: set[str] = set()
        for e in entries:
            if e.is_dir:
                continue
            rel = relative_after_prefix(e.archive_path, prefix)
            if not rel:
                continue
            validate_relative_path(rel)
            key = rel.casefold()
            if key in seen_targets:
                conflicts += 1
                continue
            seen_targets.add(key)
            paths.append(rel)
            existing_item = existing.get(key)
            if existing_item:
                mapped += 1
                existing_path, old_hash = existing_item
                if old_hash == archive_hashes[e.archive_path]:
                    unchanged += 1
            else:
                new += 1
            # A file cannot safely replace an existing directory.
            target = project_root / Path(*PurePosixPath(rel).parts)
            if target.exists() and not target.is_file():
                conflicts += 1

        if conflicts or not paths:
            continue
        considered = len(paths)
        # Strongly prefer candidates that explain the archive and reuse existing files,
        # while treating unchanged files as more reliable evidence than arbitrary new files.
        score = (unchanged * 12 + mapped * 5 + new * 1) / max(1, considered)
        candidates.append(
            ZipCandidate(prefix, mapped, new, unchanged,
                         conflicts, considered, score)
        )

    candidates.sort(
        key=lambda c: (
            c.considered_count,
            c.unchanged_count,
            c.mapped_count,
            c.score,
            -c.new_count,
            -c.prefix.count("/"),
        ),
        reverse=True,
    )
    return entries, candidates
