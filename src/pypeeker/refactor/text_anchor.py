"""Shared byte-anchoring helpers for the intent-anchored planners (TASK-124).

Ported verbatim (behavior-for-behavior) from the historic
superseded fix protocol — every builtin ``check`` fix re-read the
file, verified the stored index still describes the bytes on disk, and
re-located its target from the *current* index rather than from offsets
captured at detection time. The planners in :mod:`pypeeker.refactor.delete`,
:mod:`pypeeker.refactor.imports_ops`, :mod:`pypeeker.refactor.literals`,
:mod:`pypeeker.refactor.text_ops`, and :mod:`pypeeker.refactor.docstring_ops`
share that same discipline, so the low-level byte arithmetic lives once here
instead of five times.

The file-existence/index-freshness half of that discipline (the historic
``_current_state`` check) now lives in
:class:`~pypeeker.refactor.preconditions.AnchorFileExists` /
:class:`~pypeeker.refactor.preconditions.AnchorIndexFresh` (TASK-125) — every
planner's ``check --fix`` decline for those two legacy slugs
(``"file-missing"`` / ``"stale-index"``) goes through that pair, so this
module keeps the byte-offset arithmetic each planner's re-anchoring still
needs afterwards, plus one CST predicate the delete-symbol planner and the
move-symbol preconditions share (:func:`is_definition_header`).
"""

from __future__ import annotations


def position_to_byte_offset(content: bytes, line: int, column: int) -> int | None:
    """0-indexed line/byte-column to byte offset; ``None`` when out of range.

    The one implementation of this arithmetic: the rename planner's private
    ``_position_to_byte_offset`` wraps it and raises instead, because for a
    rename an out-of-range index location is a bug, while for a replannable
    anchor it is an anchor miss, not an error.
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
    """Byte offset of the start of every physical line in ``content``.

    Lines are ``b"\\n"``-delimited, and a trailing newline does not start an
    extra empty line, so a newline-terminated file has exactly as many
    offsets as it has lines. Callers that need "the end of line ``n``" take
    ``offsets[n + 1]`` when it exists and ``len(content)`` otherwise — the
    same span either way (see :func:`line_end`).
    """
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
    "is_definition_header",
    "line_end",
    "line_start_offsets",
    "position_to_byte_offset",
]
