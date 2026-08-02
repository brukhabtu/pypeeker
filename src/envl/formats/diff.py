"""Summarizer for unified diffs: per-file +/- stat, hunk index, line ranges.

Emits **no jq recipes**. A diff is not JSON, and handing a model ``jq`` for one
wastes a call; the useful handles are ``git diff -- <path>`` for current state
and a ``sed`` line range into the immutable blob for the snapshot.

The ``git`` recipe is *rebuilt* from the captured command rather than echoed
with a pathspec appended. Appending is silently wrong on command shapes that
are already in the fixture corpus: ``git diff HEAD -- f.py | head -160`` would
become ``git diff HEAD -- <p> | head -160 -- <p>``, which exits 0 and prints
the file's *contents* instead of a diff; ``git diff --cached f.py`` would
become a ``fatal: bad revision``; and a command that already carries ``--
<paths>`` would get a second ``--`` and return every file in the original
pathspec — the whole fat payload the envelope existed to avoid. When the
command cannot be rebuilt safely, no git recipe is offered at all and the
blob's line ranges are the drill-in handle.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from envl.capture import Capture
from envl.config import CommandRule
from envl.formats._base import Summary, take

_GIT_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_PLUS_FILE = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+?)(?:\t.*)?$")
# A pipe, redirect, chain or substitution means the captured string is a shell
# fragment, not an argv: nothing can be appended to it safely.
_SHELL_META = re.compile(r"[|;&<>`$()\n]")
_MAX_GIT_SUBCOMMAND_INDEX = 3


def _summarize_diff(capture: Capture, blob_path: Path, rule: CommandRule) -> Summary:
    """Count added/removed lines and hunks per file and index their blob ranges.

    The per-file list is truncated by slicing the list of file records before
    serialization; the dropped tail is recorded as a :class:`Truncation`.
    """
    lines = capture.output.splitlines()
    files = _parse_files(lines)
    totals = {
        "files": len(files),
        "added": sum(f["added"] for f in files),
        "removed": sum(f["removed"] for f in files),
        "hunks": sum(f["hunks"] for f in files),
    }
    shown, dropped = take(files, rule.max_items or 0, "summary.files")
    body = {"totals": totals, "files": shown}
    return Summary(
        format="diff",
        body=body,
        truncation=() if dropped is None else (dropped,),
        recipes=_recipes(blob_path, shown, capture.command, capture.cwd),
    )


def _parse_files(lines: list[str]) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for number, line in enumerate(lines, start=1):
        header = _GIT_HEADER.match(line)
        if header is not None:
            current = _new_file(header.group("b"), number)
            files.append(current)
            continue
        if line.startswith("+++ "):
            named = _PLUS_FILE.match(line)
            path = named.group("path") if named else "?"
            if current is None or int(current["hunks"]) > 0:
                # A headerless unified diff starts a new file at each `+++`.
                current = _new_file(path, number)
                files.append(current)
            else:
                current["path"] = path
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            current["hunks"] = int(current["hunks"]) + 1
        elif line.startswith("+"):
            current["added"] = int(current["added"]) + 1
        elif line.startswith("-") and not line.startswith("---"):
            current["removed"] = int(current["removed"]) + 1
    _assign_ranges(files, len(lines))
    return files


def _assign_ranges(files: list[dict[str, object]], total_lines: int) -> None:
    for position, entry in enumerate(files):
        start = int(entry["blob_lines"][0])  # type: ignore[index]
        if position + 1 < len(files):
            end = int(files[position + 1]["blob_lines"][0]) - 1  # type: ignore[index]
        else:
            end = total_lines
        entry["blob_lines"] = [start, max(start, end)]


def _new_file(path: str, start_line: int) -> dict[str, object]:
    return {
        "path": path,
        "added": 0,
        "removed": 0,
        "hunks": 0,
        "blob_lines": [start_line, start_line],
    }


def _recipes(
    blob_path: Path, files: list[dict[str, object]], command: str, cwd: str
) -> tuple[str, ...]:
    blob = str(blob_path)
    base = _git_base(command, cwd)
    recipes: list[str] = []
    for entry in files[:3]:
        path = str(entry["path"])
        if base is not None:
            recipes.append(f"{base} -- {shlex.quote(path)}")
        else:
            span = entry["blob_lines"]
            recipes.append(f"sed -n '{span[0]},{span[1]}p' {blob}")  # type: ignore[index]
    recipes.append(f"grep -n '^diff --git' {blob}")
    if files and base is not None:
        span = files[0]["blob_lines"]
        recipes.append(f"sed -n '{span[0]},{span[1]}p' {blob}")  # type: ignore[index]
    return tuple(recipes)


def _git_base(command: str, cwd: str) -> str | None:
    """Rebuild the captured command as a scopable ``git diff …`` prefix.

    Returns ``None`` — meaning "offer no git recipe" — whenever the command is
    a shell fragment, is not a ``git diff``, or cannot be parsed. Silence is
    the right answer there: a recipe that looks authoritative and prints the
    wrong thing costs more than no recipe at all.

    Existing pathspec operands are dropped so the caller can append its own
    ``-- <path>``. A bare operand is told apart from a revision by asking the
    filesystem, which is what git itself does: ``src/pkg/mod.py`` exists and is
    a pathspec, ``HEAD`` and ``origin/main`` do not and are revisions.
    """
    if _SHELL_META.search(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "git" or "diff" not in tokens:
        return None
    subcommand = tokens.index("diff")
    if subcommand > _MAX_GIT_SUBCOMMAND_INDEX:
        return None
    kept = tokens[: subcommand + 1]
    for token in tokens[subcommand + 1 :]:
        if token == "--":
            break
        if token.startswith("-") or not _is_pathspec(token, cwd):
            kept.append(token)
    return shlex.join(kept)


def _is_pathspec(token: str, cwd: str) -> bool:
    try:
        return (Path(cwd) / token).exists()
    except OSError:
        return False
