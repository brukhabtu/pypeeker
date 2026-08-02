"""Content-addressed blob store plus an append-only capture manifest.

Blobs live at ``<root>/blobs/<sha256[:2]>/<sha256>`` so identical outputs
dedupe for free, and the manifest at ``<root>/manifest.jsonl`` records one JSON
line per capture. The root is always outside any repository (see
:func:`envl.config.default_cache_dir`).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from envl.capture import Capture


@dataclass(frozen=True)
class BlobRecord:
    """Where a capture's full output was stored, and whether it already existed.

    ``deduped`` is true when an identical payload was already on disk, so no
    bytes were rewritten. The blob is immutable: the same digest always names
    the same content.
    """

    digest: str
    path: Path
    bytes: int
    deduped: bool


class BlobCache:
    """Immutable snapshot store for full command output.

    **Write-only by design.** ``BlobCache`` has no lookup-by-command method and
    must never grow one. Adding a "serve the cached output for this command"
    fast path would be a silent-wrong-data correctness bug: agents edit files
    constantly, so a previous run's snapshot presented as live output is wrong
    in a way the caller cannot detect. The blob is a snapshot labelled with its
    capture time; wanting current state means re-running the command.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def blobs_dir(self) -> Path:
        """Directory holding the sharded content-addressed blobs."""
        return self._root / "blobs"

    @property
    def manifest_path(self) -> Path:
        """Path of the append-only JSON-lines capture manifest."""
        return self._root / "manifest.jsonl"

    def store(self, capture: Capture, fmt: str) -> BlobRecord:
        """Write ``capture``'s output as a blob and append a manifest line.

        The manifest line is appended on every call, including when the payload
        deduped against an existing blob — two runs of the same command that
        produced identical output are two distinct captures and both are part
        of the record.
        """
        payload = capture.output.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        shard = self.blobs_dir / digest[:2]
        target = shard / digest
        deduped = target.exists()
        if not deduped:
            shard.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, payload)
        else:
            # A blob's mtime is its last-*used* time, not its first-written
            # time. Without this touch, a command whose output is stable —
            # a passing `pytest -q`, a clean `pypeeker check`, an unchanged
            # `git diff`, i.e. exactly what the envelope is for — keeps
            # deduping onto an ageing blob until :meth:`prune` deletes the very
            # file the envelope is about to name as "FULL OUTPUT IS ON DISK AT".
            _touch(target)
        self._append_manifest(capture, fmt, digest, target, len(payload))
        return BlobRecord(
            digest=digest, path=target, bytes=len(payload), deduped=deduped
        )

    def prune(self, max_bytes: int, ttl_days: int, keep: str | None = None) -> int:
        """Drop expired blobs, then oldest-first until under ``max_bytes``.

        Returns the number of blobs removed. ``manifest.jsonl`` is never
        touched: it is the preservation record, it is tiny, and rewriting it
        would break the append-only guarantee.

        ``keep`` is the digest of a blob the caller is about to hand out. It is
        exempt from both passes: an envelope that names a path pruning has just
        deleted is worse than an over-quota cache, because the recipe fails and
        the dropped payload is unrecoverable.
        """
        entries = [
            entry for entry in self._blob_entries() if keep is None or entry[2].name != keep
        ]
        removed = 0
        if ttl_days > 0:
            cutoff = time.time() - ttl_days * 86_400
            keep: list[tuple[float, int, Path]] = []
            for mtime, size, path in entries:
                if mtime < cutoff:
                    _unlink(path)
                    removed += 1
                else:
                    keep.append((mtime, size, path))
            entries = keep
        total = sum(size for _, size, _ in entries)
        for mtime, size, path in sorted(entries):
            if total <= max_bytes:
                break
            _unlink(path)
            total -= size
            removed += 1
        return removed

    def _blob_entries(self) -> list[tuple[float, int, Path]]:
        entries: list[tuple[float, int, Path]] = []
        if not self.blobs_dir.is_dir():
            return entries
        for path in self.blobs_dir.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            entries.append((stat.st_mtime, stat.st_size, path))
        return entries

    def _append_manifest(
        self, capture: Capture, fmt: str, digest: str, blob: Path, size: int
    ) -> None:
        record = {
            "captured_at": capture.captured_at,
            "command": capture.command,
            "argv": list(capture.argv),
            "cwd": capture.cwd,
            "exit_code": capture.exit_code,
            "bytes": size,
            "sha256": digest,
            "blob": str(blob),
            "format": fmt,
        }
        self._root.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def _atomic_write(target: Path, payload: bytes) -> None:
    handle = tempfile.NamedTemporaryFile(
        dir=str(target.parent), delete=False, suffix=".tmp"
    )
    try:
        with handle:
            handle.write(payload)
        os.replace(handle.name, target)
    except BaseException:
        _unlink(Path(handle.name))
        raise


def _touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        # A cache whose mtime cannot be refreshed is still a usable cache; the
        # blob is what matters and it is already on disk.
        pass


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
