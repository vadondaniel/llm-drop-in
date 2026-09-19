from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
import zipfile


TEMP_SUFFIXES = (".crdownload", ".part", ".tmp")


@dataclass(frozen=True)
class ZipSignature:
    size: int
    mtime_ns: int


@dataclass
class _Candidate:
    path: Path
    signature: ZipSignature
    stable_since: float
    first_seen: float
    next_validation: float
    attempts: int = 0


class DownloadFolderMonitor:
    """Detect newly appeared ZIPs and release them only after they are stable."""

    def __init__(
        self,
        folder: Path,
        *,
        stable_for: float = 1.5,
        retry_every: float = 0.75,
        max_wait: float = 15 * 60,
    ) -> None:
        self.folder = Path(folder).expanduser()
        self.stable_for = max(0.1, stable_for)
        self.retry_every = max(0.1, retry_every)
        self.max_wait = max(self.stable_for, max_wait)
        self._known: dict[Path, ZipSignature] = {}
        self._pending: dict[Path, _Candidate] = {}
        self._started = False

    @property
    def pending(self) -> tuple[Path, ...]:
        return tuple(sorted(self._pending, key=lambda path: path.name.casefold()))

    def set_folder(self, folder: Path, *, seed_existing: bool = True) -> None:
        self.folder = Path(folder).expanduser()
        self._known.clear()
        self._pending.clear()
        if seed_existing:
            self._seed_existing()
        self._started = True

    def start(self) -> None:
        self._known.clear()
        self._pending.clear()
        self._seed_existing()
        self._started = True

    def stop(self) -> None:
        self._pending.clear()
        self._known.clear()
        self._started = False

    def poll(self, now: float | None = None) -> list[Path]:
        now = time.monotonic() if now is None else now
        if not self._started:
            return []
        folder = self.folder
        if not folder.is_dir():
            return []

        current = self._snapshot(folder)

        for path in list(self._known):
            if path not in current:
                self._known.pop(path, None)
                self._pending.pop(path, None)

        for path, signature in current.items():
            previous = self._known.get(path)
            if previous is None:
                self._known[path] = signature
                self._pending[path] = _Candidate(
                    path=path,
                    signature=signature,
                    stable_since=now,
                    first_seen=now,
                    next_validation=now,
                )
            elif previous != signature:
                self._known[path] = signature
                self._pending[path] = _Candidate(
                    path=path,
                    signature=signature,
                    stable_since=now,
                    first_seen=now,
                    next_validation=now,
                )

        ready: list[Path] = []
        for path in sorted(self._pending, key=lambda item: item.name.casefold()):
            candidate = self._pending.get(path)
            signature = current.get(path)
            if candidate is None or signature is None:
                continue

            if signature != candidate.signature:
                candidate.signature = signature
                candidate.stable_since = now
                candidate.first_seen = now
                candidate.next_validation = now
                candidate.attempts = 0
                continue

            if now < candidate.next_validation:
                continue
            if now - candidate.first_seen > self.max_wait:
                self._pending.pop(path, None)
                continue
            if now - candidate.stable_since < self.stable_for:
                candidate.next_validation = candidate.stable_since + self.stable_for
                continue

            candidate.attempts += 1
            if self._is_download_still_in_progress(path):
                candidate.next_validation = now + self.retry_every
                continue
            if not self._is_complete_zip(path):
                candidate.next_validation = now + self.retry_every
                continue

            ready.append(path)
            self._pending.pop(path, None)

        return ready

    def _seed_existing(self) -> None:
        if not self.folder.is_dir():
            return
        self._known = self._snapshot(self.folder)
        self._pending.clear()

    @staticmethod
    def _snapshot(folder: Path) -> dict[Path, ZipSignature]:
        result: dict[Path, ZipSignature] = {}
        try:
            entries = list(folder.iterdir())
        except OSError:
            return result
        for path in entries:
            if not path.is_file() or path.suffix.lower() != ".zip":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            result[path.resolve()] = ZipSignature(
                stat.st_size, stat.st_mtime_ns)
        return result

    @staticmethod
    def _is_download_still_in_progress(path: Path) -> bool:
        for suffix in TEMP_SUFFIXES:
            sibling = Path(str(path) + suffix)
            if sibling.exists():
                return True
        return False

    @staticmethod
    def _is_complete_zip(path: Path) -> bool:
        try:
            if path.stat().st_size <= 0:
                return False
            with zipfile.ZipFile(path) as archive:
                archive.infolist()
            return True
        except (OSError, zipfile.BadZipFile, EOFError):
            return False
