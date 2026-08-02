"""Summarizers for plain text and for grep/rg search results.

Neither gets jq recipes: line ranges (``sed``) and ``grep`` are the drill-in
handles for line-oriented output. Search results additionally get a per-file
match tally, because "which files matched" is usually the question and it is
far cheaper than the matching lines themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

from envl.capture import Capture
from envl.config import CommandRule
from envl.formats._base import Summary, Truncation, excerpt, take

_TAIL_LINES = 5
# The path may contain spaces — `backlog/tasks/task-14 - a title.md` is an
# ordinary grep hit in this repo. A `[^\s:]+` path pattern silently drops those
# lines from `matches`, from `files` and from the totals in the LOUD marker,
# which is the silent-wrong-data failure the envelope must never produce. What
# a path may not contain is a colon, since that is the field separator.
_MATCH_LINE = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<line>\d+)[:-]")


def _summarize_text(capture: Capture, blob_path: Path, rule: CommandRule) -> Summary:
    """Report the line count with a head/tail excerpt and line-range recipes.

    The head is truncated by slicing the list of lines before serialization.
    """
    lines = capture.output.splitlines()
    max_lines = rule.max_lines or 0
    max_line_chars = rule.max_line_chars or 0
    body: dict[str, object] = {
        "lines": len(lines),
        "head": excerpt(lines, max_lines, max_line_chars),
    }
    if len(lines) > max_lines + _TAIL_LINES:
        body["tail"] = excerpt(lines[-_TAIL_LINES:], _TAIL_LINES, max_line_chars)
    truncation: tuple[Truncation, ...] = ()
    if len(lines) > max_lines:
        truncation = (
            Truncation(field="summary.head", shown=max_lines, total=len(lines)),
        )
    blob = str(blob_path)
    recipes = (
        f"sed -n '{min(max_lines + 1, len(lines) or 1)},{len(lines) or 1}p' {blob}",
        f"grep -n 'PATTERN' {blob}",
        f"wc -l {blob}",
    )
    return Summary(format="text", body=body, truncation=truncation, recipes=recipes)


def _summarize_search(capture: Capture, blob_path: Path, rule: CommandRule) -> Summary:
    """Tally matches per file and excerpt the first matching lines.

    Both the per-file list and the head excerpt are truncated by slicing lists
    before serialization; each dropped tail is a recorded :class:`Truncation`.
    """
    lines = capture.output.splitlines()
    max_items = rule.max_items or 0
    max_lines = rule.max_lines or 0
    max_line_chars = rule.max_line_chars or 0

    per_file: dict[str, int] = {}
    matches = 0
    for line in lines:
        found = _MATCH_LINE.match(line)
        if found is None:
            continue
        matches += 1
        path = found.group("path")
        per_file[path] = per_file.get(path, 0) + 1

    ranked = [
        {"path": path, "matches": count}
        for path, count in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    shown, dropped_files = take(ranked, max_items, "summary.files")
    truncation: list[Truncation] = []
    if dropped_files is not None:
        truncation.append(dropped_files)
    if len(lines) > max_lines:
        truncation.append(
            Truncation(field="summary.head", shown=max_lines, total=len(lines))
        )

    body: dict[str, object] = {
        "matches": matches,
        "lines": len(lines),
        "files": shown,
        "head": excerpt(lines, max_lines, max_line_chars),
    }
    blob = str(blob_path)
    recipes = [f"grep -c '' {blob}", f"sed -n '1,{max(len(lines), 1)}p' {blob}"]
    if shown:
        recipes.append(f"grep -n '^{shown[0]['path']}:' {blob}")
    return Summary(
        format="search",
        body=body,
        truncation=tuple(truncation),
        recipes=tuple(recipes),
    )
