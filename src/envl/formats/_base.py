"""Shared types and clipping helpers for the format summarizers.

Every summarizer truncates by slicing a **list before serialization** and
recording a :class:`Truncation`; nothing here ever slices a serialized JSON
string, which is what makes "always valid JSON, even when truncated" fall out
for free rather than being defended by a length check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ELLIPSIS = "…"


@dataclass(frozen=True)
class Truncation:
    """A record that ``field`` showed ``shown`` of ``total`` items.

    One of these per truncated collection. The envelope turns a non-empty
    tuple of these into its LOUD ``TRUNCATED`` marker.
    """

    field: str
    shown: int
    total: int


@dataclass(frozen=True)
class Summary:
    """A format-specific digest of one capture, plus its drill-in recipes.

    ``body`` is a plain JSON-serializable mapping. ``recipes`` are shell
    strings, the cheapest encoding a model can paste; recipes naming the blob
    path read the immutable snapshot, recipes naming the original command read
    current state.
    """

    format: str
    body: dict[str, Any] = field(default_factory=dict)
    truncation: tuple[Truncation, ...] = ()
    recipes: tuple[str, ...] = ()


def clip_line(text: str, limit: int) -> str:
    """Clip one excerpt line to ``limit`` characters with a trailing ellipsis.

    This is a string slice, but of a **list element before serialization**, so
    JSON validity is untouched — it cannot cut a token in the serialized
    document because serialization has not happened yet.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + ELLIPSIS


def take(items: list[Any], limit: int, name: str) -> tuple[list[Any], Truncation | None]:
    """Return the first ``limit`` of ``items`` plus a Truncation when any dropped."""
    if limit < 0:
        limit = 0
    if len(items) <= limit:
        return items, None
    return items[:limit], Truncation(field=name, shown=limit, total=len(items))


def excerpt(lines: list[str], limit: int, max_line_chars: int) -> list[str]:
    """Return up to ``limit`` lines, each clipped to ``max_line_chars``."""
    return [clip_line(line, max_line_chars) for line in lines[: max(limit, 0)]]
