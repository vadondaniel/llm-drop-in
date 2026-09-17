from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import os
import subprocess
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


@dataclass(frozen=True)
class ImportAnalysis:
    filtered_entries: list[ZipEntry]
    filtered_candidates: list[ZipCandidate]
    included_entries: list[ZipEntry]
    included_candidates: list[ZipCandidate]
    archive_hashes: dict[str, str]
    existing_files: dict[str, tuple[Path, str | None]]
    ignored_archive_paths: set[str]


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
        seen_casefold: set[str] = set()
        for info in infos:
            clean = _clean_archive_path(info.filename)
            is_dir = info.is_dir() or info.filename.endswith(("/", "\\"))
            if not clean and not is_dir:
                raise ValueError("ZIP contains an invalid empty file path")
            if not clean:
                continue
            if not is_dir:
                validate_relative_path(clean)
                clean_key = clean.casefold()
                if clean_key in seen_casefold:
                    raise ValueError(
                        f"ZIP contains duplicate/case-colliding path: {clean}")
                seen.add(clean)
                seen_casefold.add(clean_key)
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


def _git_ignored_paths(project_root: Path, paths: list[str]) -> set[str]:
    """Return archive-relative paths matched by Git ignore rules.

    Git itself is used so .gitignore, nested .gitignore files, .git/info/exclude,
    and configured global excludes all follow the same matching semantics as Git.
    ``--no-index`` is important because the incoming ZIP files are not yet tracked
    in the working tree. If the project is not inside a Git work tree, or Git is
    unavailable, no paths are filtered.
    """
    if not paths:
        return set()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "check-ignore",
                "--stdin",
                "--no-index",
                "-z",
            ],
            input="\0".join(paths) + "\0",
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return set()

    # 0 = at least one path matched, 1 = no paths matched. Other statuses
    # indicate that the directory is not a Git work tree (or another Git
    # invocation problem); in that case imports continue without filtering.
    if result.returncode not in (0, 1):
        return set()
    return {path for path in result.stdout.split("\0") if path}


def _build_candidates(
    entries: list[ZipEntry],
    project_root: Path,
    archive_hashes: dict[str, str],
    existing: dict[str, tuple[Path, str | None]],
) -> list[ZipCandidate]:
    # Candidate detection used to recursively enumerate and SHA-256 every file in
    # the project. That is needlessly expensive for repositories containing
    # node_modules, build outputs, caches, or large generated files. Instead,
    # inspect only destination paths that occur in the archive, and only hash
    # files after the candidates with the largest archive coverage are known.
    provisional: list[tuple[str, int, int,
                            int, int, list[tuple[str, str]]]] = []
    max_considered = 0

    for prefix in candidate_prefixes(entries):
        mapped = new = conflicts = considered = 0
        paths: list[tuple[str, str]] = []
        seen_targets: set[str] = set()
        for entry in entries:
            if entry.is_dir:
                continue
            rel = relative_after_prefix(entry.archive_path, prefix)
            if not rel:
                continue
            validate_relative_path(rel)
            key = rel.casefold()
            if key in seen_targets:
                conflicts += 1
                continue
            seen_targets.add(key)
            paths.append((rel, entry.archive_path))
            considered += 1

            cached = existing.get(key)
            if cached is not None:
                mapped += 1
                continue

            target = project_root / Path(*PurePosixPath(rel).parts)
            if target.is_symlink() or (target.exists() and not target.is_file()):
                conflicts += 1
            elif target.is_file():
                mapped += 1
                existing[key] = (target, None)
            else:
                new += 1

        if not paths or conflicts:
            continue
        max_considered = max(max_considered, considered)
        provisional.append(
            (prefix, mapped, new, conflicts, considered, paths)
        )

    candidates: list[ZipCandidate] = []
    # The candidate sort always ranks considered_count first, so unchanged-file
    # hashes only matter for candidates tied at the maximum coverage.
    for prefix, mapped, new, conflicts, considered, paths in provisional:
        unchanged = 0
        if considered == max_considered:
            for rel, archive_path in paths:
                cached = existing.get(rel.casefold())
                if cached is None:
                    continue
                existing_path, old_hash = cached
                if old_hash is None:
                    old_hash = _hash_file(existing_path)
                    existing[rel.casefold()] = (existing_path, old_hash)
                if old_hash == archive_hashes[archive_path]:
                    unchanged += 1

        score = (unchanged * 12 + mapped * 5 + new) / max(1, considered)
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
    return candidates


def analyze_for_import(
    zip_path: Path,
    project_root: Path,
) -> ImportAnalysis:
    """Perform the expensive archive analysis once and retain reusable data.

    The returned archive hashes and lazily populated existing-file cache can be
    reused when constructing both filtered and include-ignored import plans, so
    the main UI never needs to scan or hash the project tree again.
    """
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise ValueError(
            "Project folder does not exist or is not a directory.")

    all_entries = read_zip(zip_path)
    file_paths = [e.archive_path for e in all_entries if not e.is_dir]
    ignored = _git_ignored_paths(project_root, file_paths)
    filtered_entries = [
        e for e in all_entries
        if e.is_dir or e.archive_path not in ignored
    ]

    with zipfile.ZipFile(zip_path) as zf:
        archive_hashes: dict[str, str] = {}
        for entry in all_entries:
            if entry.is_dir:
                continue
            with zf.open(entry.archive_path, "r") as src:
                digest = hashlib.sha256()
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    digest.update(chunk)
                archive_hashes[entry.archive_path] = digest.hexdigest()

    existing: dict[str, tuple[Path, str | None]] = {}
    filtered_candidates = _build_candidates(
        filtered_entries, project_root, archive_hashes, existing
    )
    included_candidates = _build_candidates(
        all_entries, project_root, archive_hashes, existing
    )
    return ImportAnalysis(
        filtered_entries=filtered_entries,
        filtered_candidates=filtered_candidates,
        included_entries=all_entries,
        included_candidates=included_candidates,
        archive_hashes=archive_hashes,
        existing_files=existing,
        ignored_archive_paths=ignored,
    )


def analyze_variants(
    zip_path: Path,
    project_root: Path,
) -> tuple[tuple[list[ZipEntry], list[ZipCandidate]], tuple[list[ZipEntry], list[ZipCandidate]]]:
    analysis = analyze_for_import(zip_path, project_root)
    return (
        (analysis.filtered_entries, analysis.filtered_candidates),
        (analysis.included_entries, analysis.included_candidates),
    )


def analyze(
    zip_path: Path,
    project_root: Path,
    *,
    include_gitignored: bool = False,
) -> tuple[list[ZipEntry], list[ZipCandidate]]:
    """Analyze an archive, optionally including files matched by Git ignore rules."""
    filtered, included = analyze_variants(zip_path, project_root)
    return included if include_gitignored else filtered
