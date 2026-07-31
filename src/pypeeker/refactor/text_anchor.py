"""Shared byte-anchoring helpers for the intent-anchored planners (TASK-124).

Ported verbatim (behavior-for-behavior) from the historic
superseded fix protocol — every builtin ``check`` fix re-read the
file, verified the stored index still describes the bytes on disk, and
re-located its target from the *current* index rather than from offsets
captured at detection time. The planners in :mod:`pypeeker.refactor.delete`,
:mod:`pypeeker.refactor.imports_ops`, :mod:`pypeeker.refactor.literals`, and
:mod:`pypeeker.refactor.text_ops` share that same discipline, so the low-level
byte arithmetic lives once here instead of four times.

:class:`AnchorStateError` carries the legacy fix-protocol
slug (``"stale-index"`` / ``"file-missing"``) so each planner's own error type
can re-raise with its own class but the same machine-readable code — the
mapping TASK-124 stage B's ``check --fix`` report contract depends on.
"""

from __future__ import annotations

from pypeeker.models import FileIndex
from pypeeker.storage import IndexStore


class AnchorStateError(Exception):
    """The file/index pair a planner needs to re-anchor against isn't usable.

    ``code`` is the legacy fix-protocol refusal slug for this
    refusal (``"file-missing"`` or ``"stale-index"``) — callers re-raise
    their own planner-specific error class with the same code, so a single
    decline vocabulary survives the port.
    """

    def __init__(self, code: str, message: str) -> None:
        """Store the machine code alongside the human-readable message."""
        super().__init__(message)
        self.code = code


def current_state(store: IndexStore, file_path: str) -> tuple[bytes, FileIndex]:
    """Current file bytes plus a verified-fresh index entry, or a decline.

    Mirrors the fix protocol's ``_current_state``: a missing file
    raises :class:`AnchorStateError` with code ``"file-missing"``; a missing
    index entry or a hash mismatch (the file changed after indexing) raises
    with code ``"stale-index"``.
    """
    source = store.project_root / file_path
    if not source.exists():
        raise AnchorStateError("file-missing", f"{file_path} no longer exists")
    index = store.load(file_path)
    if index is None:
        raise AnchorStateError("stale-index", f"{file_path} is not indexed")
    if index.file_hash != IndexStore.compute_file_hash(source):
        raise AnchorStateError(
            "stale-index",
            f"{file_path} changed since it was indexed; re-index and re-plan",
        )
    return source.read_bytes(), index


def position_to_byte_offset(content: bytes, line: int, column: int) -> int | None:
    """0-indexed line/byte-column to byte offset; ``None`` when out of range.

    Same arithmetic as :func:`pypeeker.refactor.planner.position_to_byte_offset`
    but returns ``None`` instead of raising — for a replannable anchor, an
    out-of-range detection-time location is an anchor miss, not an error.
    """
    offset = 0
    for i, file_line in enumerate(content.split(b"\n")):
        if i == line:
            if column > len(file_line):
                return None
            return offset + column
        offset += len(file_line) + 1  # +1 for the newline
    return None


def line_start_offsets(content: bytes) -> list[int]:
    """Byte offset of the start of every physical line in ``content``."""
    offsets = [0]
    for i, byte in enumerate(content):
        if byte == 0x0A and i + 1 < len(content):  # b"\n"
            offsets.append(i + 1)
    return offsets


def line_end(line_starts: list[int], content: bytes, line: int) -> int:
    """Byte offset of the end of ``line`` (its newline excluded)."""
    end = line_starts[line + 1] if line + 1 < len(line_starts) else len(content)
    return end - 1 if end > 0 and content[end - 1 : end] == b"\n" else end


def is_definition_header(header: bytes, kind: str, name: str) -> bool:
    """True when ``header`` is the ``def``/``async def``/``class`` line of ``name``."""
    stripped = header.strip()
    keywords = (b"class",) if kind == "class" else (b"def", b"async def")
    name_bytes = name.encode("utf-8")
    for keyword in keywords:
        prefix = keyword + b" " + name_bytes
        if stripped.startswith(prefix):
            rest = stripped[len(prefix) : len(prefix) + 1]
            if not rest or not (rest.isalnum() or rest == b"_"):
                return True
    return False


__all__ = [
    "AnchorStateError",
    "current_state",
    "is_definition_header",
    "line_end",
    "line_start_offsets",
    "position_to_byte_offset",
]
