from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import zipfile
from datetime import datetime

PACKAGE_WORKTREE = "worktree"
PACKAGE_STAGED = "staged"
PACKAGE_HEAD = "head"


_WINDOWS_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class PackageResult:
    path: Path
    scope: str
    files: int
    size_bytes: int
    missing: tuple[str, ...] = ()


class PackageError(RuntimeError):
    """Raised when a project package cannot be created."""


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            creationflags=_WINDOWS_NO_CONSOLE,
        )
    except OSError as exc:
        raise PackageError(f"Could not start Git: {exc}") from exc
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PackageError(message or f"Git failed: {' '.join(args)}")
    return completed.stdout


def _require_repo(root: Path) -> None:
    if not root.is_dir():
        raise PackageError(f"Project folder does not exist: {root}")
    try:
        value = _git(root, "rev-parse", "--is-inside-work-tree").decode(
            "ascii", errors="replace"
        ).strip()
    except PackageError as exc:
        raise PackageError(
            "The selected folder is not a Git working tree."
        ) from exc
    if value.lower() != "true":
        raise PackageError("The selected folder is not a Git working tree.")


def _decode_git_paths(data: bytes) -> list[str]:
    # Git's -z output is deliberately unquoted. Git path names are normally
    # UTF-8; surrogateescape preserves unusual byte sequences on disk.
    text = data.decode("utf-8", errors="surrogateescape")
    return [item for item in text.split("\0") if item]


def _worktree_files(root: Path) -> tuple[list[str], tuple[str, ...]]:
    tracked = _decode_git_paths(_git(root, "ls-files", "-z"))
    untracked = _decode_git_paths(
        _git(root, "ls-files", "-z", "--others", "--exclude-standard")
    )

    seen: set[str] = set()
    relative: list[str] = []
    for name in [*tracked, *untracked]:
        normalized = name.replace("\\", "/")
        if normalized in seen:
            continue
        seen.add(normalized)
        relative.append(normalized)

    included: list[tuple[str, Path]] = []
    missing: list[str] = []
    for name in relative:
        source = root / Path(*name.split("/"))
        if not source.is_file() or source.is_symlink():
            missing.append(name)
            continue
        included.append((name, source))

    included.sort(key=lambda pair: pair[0].casefold())
    missing.sort(key=str.casefold)
    return [name for name, _ in included], tuple(missing)


def _unique_output_path(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = root.name or "project"
    candidate = root.parent / f"{base}-llm-{stamp}.zip"
    counter = 2
    while candidate.exists():
        candidate = root.parent / f"{base}-llm-{stamp}-{counter}.zip"
        counter += 1
    return candidate


def _tree_entries(root: Path, treeish: str) -> list[tuple[str, str, str, str]]:
    """Return (mode, type, object_id, path) entries from a Git tree."""
    raw = _git(root, "ls-tree", "-r", "-z", "--full-tree", treeish)
    entries: list[tuple[str, str, str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = header.decode(
            "ascii"
        ).split(" ", 2)
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries.append((mode, object_type, object_id, path.replace("\\", "/")))
    return entries


def _archive_git_tree(root: Path, treeish: str, output: Path) -> int:
    """Write an exact tree snapshot without applying .gitattributes export-ignore."""
    entries = [
        entry for entry in _tree_entries(root, treeish)
        if entry[1] == "blob"
    ]

    process = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_WINDOWS_NO_CONSOLE,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    try:
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for mode, _object_type, object_id, path in entries:
                process.stdin.write((object_id + "\n").encode("ascii"))
                process.stdin.flush()

                header = process.stdout.readline()
                if not header:
                    raise PackageError(
                        "Git stopped while reading a tree snapshot.")
                parts = header.rstrip(b"\n").split(b" ", 2)
                if len(parts) != 3:
                    raise PackageError("Unexpected Git cat-file response.")
                _returned_id, returned_type, raw_size = parts
                if returned_type != b"blob":
                    raise PackageError(
                        f"Git returned {returned_type.decode('ascii', errors='replace')} "
                        f"for {path!r}."
                    )
                size = int(raw_size)
                info = zipfile.ZipInfo(path)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (int(mode, 8) & 0o777) << 16
                with archive.open(info, "w") as destination:
                    remaining = size
                    while remaining:
                        chunk = process.stdout.read(
                            min(1024 * 1024, remaining))
                        if not chunk:
                            raise PackageError(
                                f"Git ended the blob unexpectedly for {path!r}."
                            )
                        destination.write(chunk)
                        remaining -= len(chunk)
                if process.stdout.read(1) != b"\n":
                    raise PackageError(
                        "Git cat-file stream lost synchronization.")
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        stderr = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if return_code != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise PackageError(
                message or "Git could not read the tree snapshot.")

    return len(entries)


def _archive_worktree(root: Path, output: Path) -> tuple[int, tuple[str, ...]]:
    names, missing = _worktree_files(root)
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for name in names:
            source = root / Path(*name.split("/"))
            archive.write(source, arcname=name)
    return len(names), missing


def package_project(
    project_root: Path,
    *,
    scope: str = PACKAGE_WORKTREE,
    output_path: Path | None = None,
) -> PackageResult:
    root = project_root.resolve()
    if scope not in {PACKAGE_WORKTREE, PACKAGE_STAGED, PACKAGE_HEAD}:
        raise ValueError(f"Unknown package scope: {scope}")

    _require_repo(root)
    output = (
        output_path.resolve()
        if output_path is not None
        else _unique_output_path(root)
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        if scope == PACKAGE_WORKTREE:
            files, missing = _archive_worktree(root, output)
        elif scope == PACKAGE_HEAD:
            files = _archive_git_tree(root, "HEAD", output)
            missing = ()
        else:
            # write-tree materializes the current index as a tree object. This
            # gives us the exact staged snapshot, including staged modifications
            # but excluding unstaged working-tree changes and untracked files.
            tree = _git(root, "write-tree").decode("ascii").strip()
            if not tree:
                raise PackageError("Git did not produce a staged tree.")
            files = _archive_git_tree(root, tree, output)
            missing = ()
    except Exception:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    try:
        size_bytes = output.stat().st_size
    except OSError as exc:
        raise PackageError(
            f"Created the ZIP but could not inspect it: {exc}") from exc

    return PackageResult(
        path=output,
        scope=scope,
        files=files,
        size_bytes=size_bytes,
        missing=missing,
    )
