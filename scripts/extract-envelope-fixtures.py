#!/usr/bin/env python3
"""Extract a size-capped envelope fixture corpus from Claude Code subagent transcripts.

The transcripts hold thousands of real tool outputs, and they live outside the
repository in an ephemeral container -- they are lost when it is reclaimed. This
script preserves a sampled slice of them as test fixtures so the envelope can be
developed and measured against *real* output shapes rather than invented ones.

Usage:
    python3 scripts/extract-envelope-fixtures.py [--transcripts DIR] [--out DIR]
                                                 [--cap-bytes N] [--per-family SPEC]
                                                 [--dry-run] [--extend]

Only ``Bash`` tool results are considered: ``envl -- <command>`` wraps shell
commands, and the harness-native tools (Read, Grep, Edit) are not shell commands
and cannot be wrapped at any adoption level. Sampling that included them would
describe a corpus the envelope can never actually see.

Two things about this script are deliberate and load-bearing:

* The tool_use/tool_result pairing and the command-family table are **copied**
  from ``.claude/workflows/measure-tool-costs.py`` rather than re-derived, so
  the corpus is bucketed by exactly the same rule as the token baseline in
  ``.claude/workflows/TOKEN-COSTS.md``. That directory is read-only for this
  work, hence a copy rather than an import.
* The ``format`` label comes from ``envl.detect_format`` -- the shipping
  classifier -- not from a local heuristic. If the manifest recorded a different
  classifier's labels, the replay harness's per-family numbers would describe a
  classifier that is not the one in production.

Selection is deterministic (dedupe by sha256, then sort by size descending with
sha256 as the tiebreak, then take largest-first until the family budget is
spent). A churning corpus would make every future harness diff unreadable.

Largest-first also means the corpus is a **regression corpus, not a measurement
sample**: the envelope's reduction ratio is size-dependent, so ratios measured
here run better than the population's. ``scripts/replay-envelope.py`` therefore
measures its ratios by replaying the whole above-threshold population and uses
this corpus only to hold the adapters to captured reality in the test suite.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from envl import detect_format  # noqa: E402

EXTRACTOR_VERSION = 1

# Copied verbatim from .claude/workflows/measure-tool-costs.py (DEFAULT_DIR).
DEFAULT_TRANSCRIPTS = os.path.expanduser(
    "~/.claude/projects/-home-user-pypeeker/"
    "caa0cb8f-7cdb-5f80-986b-1bf4a3f556bf/subagents/workflows"
)

DEFAULT_OUT = Path("tests/fixtures/envelope")

# Global cap on committed fixture bytes. 400 KB, not the full ~6 MB of transcript
# payload: the corpus exists to exercise every format family against real shapes,
# and a repo does not need a multi-megabyte blob to do that.
DEFAULT_CAP_BYTES = 409_600

# Per-family budgets, summing exactly to DEFAULT_CAP_BYTES. Weighted towards the
# families whose real output is largest and least bounded (diff, text).
DEFAULT_PER_FAMILY = {
    "diff": 102_400,
    "text": 92_160,
    "search": 81_920,
    "json": 71_680,
    "pytest": 61_440,
}

# Share of a family's budget spent on its largest captures. The rest funds the
# representative tranche, so a family is not described purely by its outliers:
# `git diff` produces both 26 KB monsters and 3 KB one-file diffs, and an
# adapter that only ever sees the monsters is only half tested.
_HEAD_SHARE = 0.7

# Captures smaller than this are not envelope material: the envelope's default
# `threshold_bytes` is 2048 and output below it passes through untouched by
# design. Without the floor the corpus fills with 300-byte captures that the
# library would never wrap, which inflates the file count and tells the replay
# harness nothing.
DEFAULT_MIN_BYTES = 2_048

_EXTENSIONS = {"json": ".json", "diff": ".diff"}

# Copied verbatim from .claude/workflows/measure-tool-costs.py.
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

# Copied verbatim from .claude/workflows/measure-tool-costs.py.
# A leading `cd <path> &&` says nothing about what the command does; strip it so
# the same command in a worktree and in the primary checkout land in one family.
_CD_PREFIX = re.compile(r"^\(?cd [^&;|]+(&&|;)\s*")

# Best-effort credential denylist. Matching candidates are SKIPPED, never
# redacted: a redacted fixture is a fixture whose byte count lies, and the byte
# counts are the whole point of the replay harness.
_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"Authorization:\s*(Bearer|Basic)\s"),
    re.compile(
        r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{16,}"
    ),
]

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def command_family(command: str) -> str:
    """Bucket a shell command by the tool it actually runs.

    Copied verbatim from ``.claude/workflows/measure-tool-costs.py`` so the
    corpus and the token baseline agree on what "the git family" means.
    """
    body = _CD_PREFIX.sub("", command.strip()).strip()
    for pattern, label in COMMAND_FAMILIES:
        if re.match(pattern, body):
            return label
    parts = body.split()
    return parts[0][:18] if parts else "?"


def iter_tool_results(transcript_dir: str):
    """Yield ``(tool_name, tool_input, payload_text, is_error)`` per completed call.

    The pairing logic is copied from ``measure-tool-costs.py:iter_tool_results``;
    the only change is that the payload *text* is yielded alongside the call's
    arguments, because the corpus needs the bytes and not just their size.
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
                        name, tool_input = pending.get(
                            block.get("tool_use_id"), ("?", {})
                        )
                        yield (
                            name,
                            tool_input,
                            _payload_text(block.get("content")),
                            bool(block.get("is_error")),
                        )


def _payload_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        )
    return json.dumps(content)


def _is_secretish(payload: str) -> bool:
    return any(pattern.search(payload) is not None for pattern in _SECRET_PATTERNS)


def _slug(command: str, digest: str) -> str:
    stem = _SLUG_UNSAFE.sub("-", command.strip().lower()).strip("-")[:44].strip("-")
    return f"{stem or 'command'}-{digest[:8]}"


def _count_transcripts(transcript_dir: str) -> int:
    return len(glob.glob(os.path.join(transcript_dir, "*/agent-*.jsonl")))


def collect_candidates(
    transcript_dir: str, min_bytes: int = DEFAULT_MIN_BYTES
) -> tuple[list[dict], dict[str, int]]:
    """Scan the transcripts and return deduped Bash candidates plus scan stats."""
    by_digest: dict[str, dict] = {}
    stats = {
        "scanned": 0,
        "bash": 0,
        "rejected_for_secrets": 0,
        "duplicates": 0,
        "below_min_bytes": 0,
    }
    rejected_digests: set[str] = set()

    for name, tool_input, payload, is_error in iter_tool_results(transcript_dir):
        stats["scanned"] += 1
        if name != "Bash" or not payload:
            continue
        stats["bash"] += 1
        if len(payload.encode("utf-8")) < min_bytes:
            stats["below_min_bytes"] += 1
            continue
        command = str(tool_input.get("command", ""))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if digest in rejected_digests:
            continue
        if digest in by_digest:
            stats["duplicates"] += 1
            continue
        if _is_secretish(payload):
            rejected_digests.add(digest)
            stats["rejected_for_secrets"] += 1
            continue
        by_digest[digest] = {
            "sha256": digest,
            "command": command,
            "command_family": command_family(command),
            "format": detect_format(command, payload),
            "original_bytes": len(payload.encode("utf-8")),
            "exit_code": 1 if is_error else 0,
            "payload": payload,
        }
    return list(by_digest.values()), stats


def select(
    candidates: list[dict], per_family: dict[str, int], already: dict[str, int]
) -> dict[str, list[dict]]:
    """Pick fixtures per family within each family's byte budget, in two tranches.

    The first tranche spends ``_HEAD_SHARE`` of the budget largest-first, which
    is what "favours the largest outputs" means. The second spends what is left
    on captures nearest the surviving pool's median size, so the family also
    carries a typical example and not only its tail.

    Deterministic by construction: every ordering breaks ties on the sha256 and
    the pool is derived only from the transcripts, so the same transcript
    directory always yields the same corpus. A churning corpus would make every
    future replay-harness diff unreadable.
    """
    chosen: dict[str, list[dict]] = {family: [] for family in per_family}
    for family, budget in per_family.items():
        pool = sorted(
            (c for c in candidates if c["format"] == family),
            key=lambda c: (-c["original_bytes"], c["sha256"]),
        )
        remaining = budget - already.get(family, 0)
        head_budget = int(remaining * _HEAD_SHARE)
        leftovers: list[dict] = []
        for candidate in pool:
            size = candidate["original_bytes"]
            if size <= head_budget:
                chosen[family].append(candidate)
                head_budget -= size
                remaining -= size
            else:
                leftovers.append(candidate)
        chosen[family].extend(_representative(leftovers, remaining))
    return chosen


def _representative(pool: list[dict], budget: int) -> list[dict]:
    """Spend ``budget`` on the captures closest to ``pool``'s median size."""
    picked: list[dict] = []
    available = list(pool)
    while available:
        fits = [c for c in available if c["original_bytes"] <= budget]
        if not fits:
            break
        median_size = statistics.median(c["original_bytes"] for c in available)
        nearest = min(
            fits, key=lambda c: (abs(c["original_bytes"] - median_size), c["sha256"])
        )
        picked.append(nearest)
        budget -= nearest["original_bytes"]
        # By digest, not `list.remove`: these dicts carry the whole payload and
        # `==` on them would compare tens of kilobytes of text per step.
        available = [c for c in available if c["sha256"] != nearest["sha256"]]
    return picked


def _existing_manifest(out_dir: Path) -> dict:
    path = out_dir / "MANIFEST.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_corpus(
    out_dir: Path,
    chosen: dict[str, list[dict]],
    header: dict,
    kept: list[dict],
) -> list[dict]:
    """Write the fixture files and MANIFEST.json; return the manifest entries."""
    entries = list(kept)
    for family, picks in sorted(chosen.items()):
        family_dir = out_dir / family
        family_dir.mkdir(parents=True, exist_ok=True)
        for candidate in picks:
            extension = _EXTENSIONS.get(family, ".txt")
            name = _slug(candidate["command"], candidate["sha256"]) + extension
            (family_dir / name).write_text(candidate["payload"], encoding="utf-8")
            entries.append(
                {
                    "path": f"{family}/{name}",
                    "command": candidate["command"],
                    "command_family": candidate["command_family"],
                    "format": family,
                    "original_bytes": candidate["original_bytes"],
                    "stored_bytes": candidate["original_bytes"],
                    "exit_code": candidate["exit_code"],
                    "sha256": candidate["sha256"],
                }
            )
    entries.sort(key=lambda e: e["path"])
    total = sum(e["stored_bytes"] for e in entries)
    header["total_bytes"] = total
    document = {**header, "fixtures": entries}
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return entries


def _parse_per_family(spec: str | None) -> dict[str, int]:
    if not spec:
        return dict(DEFAULT_PER_FAMILY)
    budgets: dict[str, int] = {}
    for chunk in spec.split(","):
        family, _, value = chunk.partition("=")
        budgets[family.strip()] = int(value)
    return budgets


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transcripts", default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cap-bytes", type=int, default=DEFAULT_CAP_BYTES)
    parser.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES)
    parser.add_argument("--per-family", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--extend",
        action="store_true",
        help="Keep the existing corpus and top each family up to its budget.",
    )
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    """Extract, select, and write the fixture corpus. Returns a process exit code."""
    args = _parse_args(argv)
    if not os.path.isdir(args.transcripts):
        print(f"no such transcript directory: {args.transcripts}", file=sys.stderr)
        return 1

    per_family = _parse_per_family(args.per_family)
    if sum(per_family.values()) > args.cap_bytes:
        print(
            f"per-family budgets ({sum(per_family.values())}) exceed the global cap "
            f"({args.cap_bytes})",
            file=sys.stderr,
        )
        return 1

    out_dir = args.out
    kept: list[dict] = []
    already: dict[str, int] = {}
    known: set[str] = set()
    if args.extend:
        previous = _existing_manifest(out_dir)
        kept = list(previous.get("fixtures", []))
        for entry in kept:
            already[entry["format"]] = (
                already.get(entry["format"], 0) + entry["stored_bytes"]
            )
            known.add(entry["sha256"])

    candidates, stats = collect_candidates(args.transcripts, args.min_bytes)
    candidates = [c for c in candidates if c["sha256"] not in known]
    chosen = select(candidates, per_family, already)

    total = sum(e["stored_bytes"] for e in kept) + sum(
        c["original_bytes"] for picks in chosen.values() for c in picks
    )
    if total > args.cap_bytes:
        raise AssertionError(
            f"selection totals {total} bytes, over the {args.cap_bytes}-byte cap"
        )

    print(f"transcripts: {args.transcripts}")
    print(f"scanned {stats['scanned']} tool results ({stats['bash']} Bash)")
    print(
        f"deduped duplicates: {stats['duplicates']}; "
        f"below {args.min_bytes}-byte floor: {stats['below_min_bytes']}; "
        f"rejected for secrets: {stats['rejected_for_secrets']}"
    )
    for family in sorted(per_family):
        picks = chosen.get(family, [])
        used = sum(c["original_bytes"] for c in picks) + already.get(family, 0)
        print(f"  {family:<8} {len(picks):>3} new  {used:>7} / {per_family[family]} bytes")
    print(f"TOTAL {total} / {args.cap_bytes} bytes")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    header = {
        "snapshot_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_dir": args.transcripts,
        "transcript_files": _count_transcripts(args.transcripts),
        "tool_results_scanned": stats["scanned"],
        "bash_results_scanned": stats["bash"],
        "candidates_rejected_for_secrets": stats["rejected_for_secrets"],
        "candidates_below_min_bytes": stats["below_min_bytes"],
        "size_cap_bytes": args.cap_bytes,
        "min_fixture_bytes": args.min_bytes,
        "per_family_caps": per_family,
        "extractor_version": EXTRACTOR_VERSION,
        "exit_code_note": (
            "derived from tool_result.is_error; the transcripts do not record the "
            "numeric exit status, so a failing command is recorded as 1"
        ),
        "format_note": "labels come from envl.detect_format, the shipping classifier",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = write_corpus(out_dir, chosen, header, kept)
    print(f"wrote {len(entries)} fixtures to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
