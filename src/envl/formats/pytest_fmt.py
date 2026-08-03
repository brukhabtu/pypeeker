"""Summarizer for pytest output: counts, failing node ids, first failure inline.

Successful pytest output is already cheap (~2.3 KB); failure output is
unbounded, and that is the real exposure. So the counts and the *complete* list
of failing node ids stay inline — they are short and they are the drill-in
handles — while only the first failure block is excerpted and the rest is left
on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from envl.capture import Capture
from envl.config import CommandRule
from envl.formats._base import Summary, Truncation, excerpt, take

_COUNT = re.compile(
    r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warning|warnings)"
)
_DURATION = re.compile(r" in ([\d.]+)s")
_SHORT_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)
_FAILURE_HEADER = re.compile(r"^_{2,} (.+?) _{2,}$")
_NODE_IN_TEXT = re.compile(r"^(\S+\.py::\S+)", re.M)
_FAILURE_SECTION = re.compile(r"^=+ (?:FAILURES|ERRORS) =+$", re.M)

_COUNT_ALIASES = {"error": "errors", "warning": "warnings"}


def _summarize_pytest(capture: Capture, blob_path: Path, rule: CommandRule) -> Summary:
    """Extract pytest's tallies, every failing node id, and the first failure block.

    The first-failure excerpt is truncated by slicing the list of its lines
    before serialization and recording a :class:`Truncation`.
    """
    output = capture.output
    lines = output.splitlines()
    max_lines = rule.max_lines or 0
    max_line_chars = rule.max_line_chars or 0

    counts = _counts(output)
    body: dict[str, object] = {"counts": counts}
    duration = _DURATION.search(output)
    if duration is not None:
        body["duration_s"] = float(duration.group(1))

    truncation: list[Truncation] = []
    all_nodes = _failed_nodes(output, counts)
    # Node ids are short and are the drill-in handles, so the inline budget is
    # deliberately generous (8x max_items => 64 by default, more than any real
    # run). It is bounded rather than unlimited because an unbounded list would
    # break the envelope's size ceiling under an adversarial 900-failure run.
    nodes, dropped_nodes = take(all_nodes, (rule.max_items or 0) * 8, "summary.failed_nodes")
    body["failed_nodes"] = nodes
    if dropped_nodes is not None:
        truncation.append(dropped_nodes)

    block, block_span = _first_failure(lines)
    if block:
        body["first_failure"] = excerpt(block, max_lines, max_line_chars)
        if len(block) > max_lines:
            truncation.append(
                Truncation(
                    field="summary.first_failure", shown=max_lines, total=len(block)
                )
            )
    return Summary(
        format="pytest",
        body=body,
        truncation=tuple(truncation),
        recipes=_recipes(blob_path, nodes, block_span, len(lines)),
    )


def _counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for number, label in _COUNT.findall(output):
        key = _COUNT_ALIASES.get(label, label)
        counts[key] = int(number)
    return counts


def _failed_nodes(output: str, counts: dict[str, int]) -> list[str]:
    """Return the failing node ids in report order, with no passing test among them.

    Two sources, deliberately asymmetric. ``FAILED`` / ``ERROR`` short-summary
    lines name failures by construction and are always trusted. A bare
    ``path.py::test`` at the start of a line does **not**: ``pytest -v`` prints
    one such line per test *including every pass*, so scanning the whole
    payload for it reports a clean 15-passed run as fifteen failures — a field
    literally named ``failed_nodes`` asserting the opposite of the tally beside
    it, and a recipe telling a model to re-run a test that passed.

    So the bare pattern is applied only inside the ``FAILURES`` / ``ERRORS``
    region, and only when the tally actually reports failures or errors. A
    ``| tail -40`` capture that cut the region header keeps working: the
    short-summary lines survive it and remain the drill-in handles.
    """
    candidates = list(_SHORT_SUMMARY.findall(output))
    if counts.get("failed") or counts.get("errors"):
        region = _failure_region(output)
        if region:
            candidates.extend(_NODE_IN_TEXT.findall(region))
    nodes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            nodes.append(candidate)
    return nodes


def _failure_region(output: str) -> str:
    found = _FAILURE_SECTION.search(output)
    return "" if found is None else output[found.end() :]


def _first_failure(lines: list[str]) -> tuple[list[str], tuple[int, int]]:
    start = None
    for index, line in enumerate(lines):
        if _FAILURE_HEADER.match(line) is not None:
            if start is None:
                start = index
            elif index > start:
                return lines[start:index], (start + 1, index)
    if start is None:
        return [], (0, 0)
    return lines[start:], (start + 1, len(lines))


def _recipes(
    blob_path: Path, nodes: list[str], block_span: tuple[int, int], total_lines: int
) -> tuple[str, ...]:
    blob = str(blob_path)
    recipes: list[str] = []
    if nodes:
        recipes.append(f"uv run pytest {nodes[0]} -q --tb=short")
        recipes.append(f"grep -n '^FAILED' {blob}")
    start, end = block_span
    if end > start:
        recipes.append(f"sed -n '{start},{end}p' {blob}")
    else:
        recipes.append(f"sed -n '1,{max(total_lines, 1)}p' {blob}")
    return tuple(recipes)
