#!/usr/bin/env python3
"""Measure what tool output costs across pipeline runs.

Reads the subagent transcripts Claude Code writes per workflow run and reports
tool-result payload size, broken down by tool, by Bash command family, and by
file read. The point is to find where an agent's context is actually being
spent, so guidance and tooling changes are driven by evidence rather than by
intuition -- see TOKEN-COSTS.md for the first run's findings.

Usage:
    python3 .claude/workflows/measure-tool-costs.py [TRANSCRIPT_DIR]

TRANSCRIPT_DIR defaults to the workflows subagent directory for this project;
pass an explicit path to measure a different project or a single run.

What is counted: the serialized ``tool_result`` payload of every completed tool
call, in approximate tokens (characters / 4). That is the quantity a prompt or
tooling change can move. It is NOT the billed token count: a tool result sits in
the agent's context and is re-sent on every later turn of that agent (prompt
caching absorbs much of the repeat cost), so treat these numbers as relative
weights between call sites, not as an invoice.
"""

from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

DEFAULT_DIR = os.path.expanduser(
    "~/.claude/projects/-home-user-pypeeker/"
    "caa0cb8f-7cdb-5f80-986b-1bf4a3f556bf/subagents/workflows"
)

# First match wins; ordered so specific runners precede the generic binaries
# they are invoked through.
COMMAND_FAMILIES = [
    (r"^uv run pytest", "uv run pytest"),
    (r"^uv run ruff", "uv run ruff"),
    (r"^uv run pypeeker", "uv run pypeeker"),
    (r"^uv run python", "uv run python"),
    (r"^uv sync", "uv sync"),
    (r"^\./scripts/verify", "verify-repo.sh"),
    (r"^\S*python3?\b", "python3"),
    (r"^git\b", "git"),
    (r"^(cat|head|tail)\b", "cat/head/tail"),
    (r"^(sed|awk)\b", "sed/awk"),
    (r"^(grep|rg)\b", "grep/rg"),
    (r"^(ls|find)\b", "ls/find"),
    (r"^backlog\b", "backlog"),
    (r"^(mkdir|cp|mv|rm|chmod|touch|echo|printf)\b", "file ops/echo"),
    (r"^(diff|wc|sort|uniq|jq)\b", "diff/wc/jq"),
]

# A leading `cd <path> &&` says nothing about what the command does; strip it so
# the same command in a worktree and in the primary checkout land in one family.
_CD_PREFIX = re.compile(r"^\(?cd [^&;|]+(&&|;)\s*")


def command_family(command: str) -> str:
    """Bucket a shell command by the tool it actually runs."""
    body = _CD_PREFIX.sub("", command.strip()).strip()
    for pattern, label in COMMAND_FAMILIES:
        if re.match(pattern, body):
            return label
    parts = body.split()
    return parts[0][:18] if parts else "?"


def iter_tool_results(transcript_dir: str):
    """Yield ``(tool_name, tool_input, approx_tokens)`` for every completed call.

    Pairs each ``tool_result`` back to the ``tool_use`` that produced it, so the
    call's arguments (which file, which command) travel with its output size.
    """
    for path in sorted(glob.glob(os.path.join(transcript_dir, "*/agent-*.jsonl"))):
        pending: dict[str, tuple[str, dict]] = {}
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (entry.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        pending[block.get("id")] = (
                            block.get("name", "?"),
                            block.get("input") or {},
                        )
                    elif block.get("type") == "tool_result":
                        name, tool_input = pending.get(block.get("tool_use_id"), ("?", {}))
                        size = len(json.dumps(block.get("content"))) // 4
                        yield name, tool_input, size


def main(argv: list[str]) -> int:
    transcript_dir = argv[1] if len(argv) > 1 else DEFAULT_DIR
    if not os.path.isdir(transcript_dir):
        print(f"no such transcript directory: {transcript_dir}", file=sys.stderr)
        return 1

    by_tool: collections.Counter[str] = collections.Counter()
    calls_by_tool: collections.Counter[str] = collections.Counter()
    by_command: collections.Counter[str] = collections.Counter()
    calls_by_command: collections.Counter[str] = collections.Counter()
    by_file: collections.Counter[tuple[str, bool]] = collections.Counter()
    calls_by_file: collections.Counter[tuple[str, bool]] = collections.Counter()

    for name, tool_input, size in iter_tool_results(transcript_dir):
        by_tool[name] += size
        calls_by_tool[name] += 1
        if name == "Bash":
            family = command_family(str(tool_input.get("command", "")))
            by_command[family] += size
            calls_by_command[family] += 1
        elif name == "Read":
            path = str(tool_input.get("file_path", "?"))
            # Worktree copies are the same file for cost purposes.
            path = re.sub(r"/home/user/pypeeker(-wt\d+)?", "", path)
            whole = "limit" not in tool_input and "offset" not in tool_input
            by_file[(path, whole)] += size
            calls_by_file[(path, whole)] += 1

    total = sum(by_tool.values())
    if not total:
        print(f"no tool results found under {transcript_dir}", file=sys.stderr)
        return 1

    def table(title, totals, counts, fmt=str):
        print(f"\n{title}")
        print(f"  {'':<26}{'KTOK':>8}{'CALLS':>8}{'AVG':>8}{'SHARE':>8}")
        for key, value in totals.most_common(12):
            share = 100 * value / total
            print(
                f"  {fmt(key):<26}{value // 1000:>8}{counts[key]:>8}"
                f"{value // counts[key]:>8}{share:>7.1f}%"
            )

    print(f"transcripts: {transcript_dir}")
    print(f"TOTAL tool-result payload: {total // 1000}k approx tokens")
    table("BY TOOL", by_tool, calls_by_tool)
    table("BASH BY COMMAND FAMILY", by_command, calls_by_command)
    table(
        "READ BY FILE",
        by_file,
        calls_by_file,
        fmt=lambda k: ("WHOLE " if k[1] else "range ") + k[0][-19:],
    )

    whole_tok = sum(v for (_, whole), v in by_file.items() if whole)
    whole_calls = sum(c for (_, whole), c in calls_by_file.items() if whole)
    range_tok = sum(v for (_, whole), v in by_file.items() if not whole)
    range_calls = sum(c for (_, whole), c in calls_by_file.items() if not whole)
    print("\nREAD SHAPE")
    print(
        f"  whole-file {whole_calls:>5} calls {whole_tok // 1000:>6}k "
        f"avg {whole_tok // max(whole_calls, 1)}"
    )
    print(
        f"  ranged     {range_calls:>5} calls {range_tok // 1000:>6}k "
        f"avg {range_tok // max(range_calls, 1)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
