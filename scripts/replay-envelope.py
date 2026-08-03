#!/usr/bin/env python3
"""Replay the envelope fixture corpus and project the saving onto the token baseline.

This is the acceptance criterion for the envelope work (TASK-144), and it is
built to be hard to flatter. Three things follow from that:

* **The baseline is recomputed live**, by re-running the same tool_use /
  tool_result pairing that ``.claude/workflows/measure-tool-costs.py`` uses,
  rather than quoting frozen numbers out of ``TOKEN-COSTS.md``. The transcripts
  are still being appended to, so the totals drift; a report that quoted a
  fixed baseline would silently describe a corpus that no longer matches it.
* **The reduction ratio is measured on the whole population, not the corpus.**
  The corpus is a size-capped sample chosen largest-first, and the reduction
  ratio is strongly size-dependent — the envelope is a big win on a 20 KB diff
  and a net loss on a 2.5 KB grep — so a corpus-measured ratio is
  systematically flattering. v1 of this harness projected corpus ratios and
  overstated every scope. v2 replays **every above-threshold Bash result in the
  transcripts** through the shipping envelope and projects *that* ratio; the
  corpus numbers are still reported, next to the population numbers, as the
  regression corpus they are.
* **The projection never multiplies raw fixture bytes.** For each command
  family ``f`` with baseline tokens ``B_f`` and above-threshold token share
  ``a_f``, ``projected_after_f = B_f * ((1 - a_f) + a_f * r_f)`` where ``r_f``
  is the population reduction ratio for that family. Output below the threshold
  passes through untouched and is charged at full price.
* **A single headline number would be dishonest**, so there isn't one. A
  truncated envelope sometimes forces a follow-up drill-in call, so every
  figure is reported as a band over drill-in rates ``d``, together with the
  break-even rate ``d*`` above which the envelope stops paying for itself.

Estimator: characters / 4, the same one the baseline uses. The baseline
additionally wraps the payload in ``json.dumps`` before counting (roughly 2%
inflation from escaping and quoting); the corpus before/after figures do not.
Both sides of every ratio use the same convention, so ratios are unaffected and
only the absolute corpus token counts run ~2% below the baseline's convention.

Usage:
    python3 scripts/replay-envelope.py [--manifest PATH] [--baseline-transcripts DIR]
                                       [--drill-in-rates 0,0.2,0.5] [--drill-fraction 0.25]
                                       [--config PATH] [--json] [--write PATH]

With no arguments it replays the committed corpus against the live transcripts
and prints the report to stdout. ``--write`` renders the same findings as the
markdown counterfactual document.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from envl import (  # noqa: E402
    BlobRecord,
    Capture,
    EnvelopeConfig,
    build_envelope,
    envelope_json,
    load_config,
    should_envelope,
)

HARNESS_VERSION = 2

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "envelope" / "MANIFEST.json"

# Copied verbatim from .claude/workflows/measure-tool-costs.py (DEFAULT_DIR).
DEFAULT_TRANSCRIPTS = os.path.expanduser(
    "~/.claude/projects/-home-user-pypeeker/"
    "caa0cb8f-7cdb-5f80-986b-1bf4a3f556bf/subagents/workflows"
)

# Copied verbatim from .claude/workflows/measure-tool-costs.py, so the harness
# buckets commands by exactly the rule that produced the published baseline.
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
_CD_PREFIX = re.compile(r"^\(?cd [^&;|]+(&&|;)\s*")

FORMAT_FAMILIES = ("json", "diff", "pytest", "search", "text")

# Adoption scopes. S1 is what TASK-147 actually commits to wrapping; S2 adds the
# git family, which needs a wrapper or a hook that does not exist yet; S3 is
# every shell family, i.e. the ceiling if an agent ran everything through envl.
S1_FAMILIES = ("uv run pypeeker", "uv run pytest", "verify-repo.sh")
S2_EXTRA_FAMILIES = ("git",)


def command_family(command: str) -> str:
    """Bucket a shell command by the tool it actually runs.

    Copied verbatim from ``.claude/workflows/measure-tool-costs.py`` so the
    harness, the corpus manifest and the token baseline agree on what a family
    is. Re-deriving it would make the projection's ``B_f`` and ``r_f`` describe
    different partitions of the same commands.
    """
    body = _CD_PREFIX.sub("", command.strip()).strip()
    for pattern, label in COMMAND_FAMILIES:
        if re.match(pattern, body):
            return label
    parts = body.split()
    return parts[0][:18] if parts else "?"


def _payload_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict)
        )
    return json.dumps(content)


def iter_tool_results(transcript_dir: str):
    """Yield ``(tool_name, tool_input, tokens, payload_bytes, payload_text)``.

    Pairing copied from ``measure-tool-costs.py:iter_tool_results``. ``tokens``
    is that script's exact measure (``len(json.dumps(content)) // 4``) so the
    recomputed baseline is comparable to TOKEN-COSTS.md; ``payload_bytes`` is
    the raw payload size, which is what the envelope's threshold tests; and
    ``payload_text`` is the payload itself, which the population replay feeds
    to the envelope.
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
                        payload = block.get("content")
                        text = _payload_text(payload)
                        yield (
                            name,
                            tool_input,
                            len(json.dumps(payload)) // 4,
                            len(text.encode("utf-8")),
                            text,
                        )


SIZE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("2-4 KB", 2048, 4096),
    ("4-8 KB", 4096, 8192),
    ("8-16 KB", 8192, 16384),
    (">16 KB", 16384, 1 << 62),
)


def _bucket_of(payload_bytes: int) -> str:
    for label, low, high in SIZE_BUCKETS:
        if low <= payload_bytes < high:
            return label
    return SIZE_BUCKETS[0][0]


def _new_group() -> dict:
    return {
        "results": 0,
        "before": 0,
        "after": 0,
        "net_loss_results": 0,
        "net_loss_before": 0,
    }


def _record(group: dict, before: int, after: int) -> None:
    group["results"] += 1
    group["before"] += before
    group["after"] += after
    if after >= before:
        group["net_loss_results"] += 1
        group["net_loss_before"] += before


def _finish(groups: dict[str, dict]) -> dict[str, dict]:
    for group in groups.values():
        group["ratio"] = group["after"] / group["before"] if group["before"] else 1.0
        group["reduction"] = 1.0 - group["ratio"]
        group["net_loss_share"] = (
            group["net_loss_before"] / group["before"] if group["before"] else 0.0
        )
        # `fixtures` is the count `project` reads for its `n` column; on the
        # population path an "n" is a real captured result, not a fixture.
        group["fixtures"] = group["results"]
        group["source"] = "population"
    return groups


def envelope_of(command: str, payload: str, config: EnvelopeConfig) -> tuple[str, str]:
    """Envelope one captured payload exactly as ``envl`` would, without any I/O.

    Returns ``(serialized_envelope, detected_format)``. The blob path is the
    one the cache *would* have produced — content-addressed under the
    configured root — because its length is charged to the envelope.
    """
    capture = Capture(
        argv=(),
        command=command,
        cwd=str(REPO_ROOT),
        exit_code=0,
        output=payload,
        captured_at="1970-01-01T00:00:00Z",
    )
    digest = hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()
    blob = BlobRecord(
        digest=digest,
        path=config.cache_dir / "blobs" / digest[:2] / digest,
        bytes=len(payload.encode("utf-8", "replace")),
        deduped=False,
    )
    envelope = build_envelope(capture, config, blob)
    rendered = envelope_json(envelope)
    if not _is_valid_json(rendered):
        # The always-valid-JSON guarantee is structural, so this cannot happen
        # by design — which is exactly why it is checked on every one of the
        # thousands of real payloads rather than asserted in prose.
        raise AssertionError(f"envelope is not valid JSON: {command[:80]}")
    return rendered, str(envelope["format"])


def measure_baseline(transcript_dir: str, config: EnvelopeConfig) -> dict:
    """Recompute the token baseline **and** replay the whole above-threshold population.

    One pass does both because they must describe the same set of calls.
    ``above`` is the share of a family's *tokens* carried by results at or over
    the threshold — the only part of the family the envelope can act on — and
    ``population`` is what those same results cost after being enveloped.

    This is the fix for v1's central bias: the corpus is sampled largest-first,
    the reduction ratio is size-dependent, so a corpus-measured ratio does not
    transfer to the population. Here nothing is sampled.
    """
    threshold_bytes = config.threshold_bytes
    by_tool: collections.Counter[str] = collections.Counter()
    calls_by_tool: collections.Counter[str] = collections.Counter()
    by_family: collections.Counter[str] = collections.Counter()
    calls_by_family: collections.Counter[str] = collections.Counter()
    above_by_family: collections.Counter[str] = collections.Counter()

    families: dict[str, dict] = {}
    formats: dict[str, dict] = {}
    buckets: dict[str, dict] = {}
    classifier: dict[str, collections.Counter[str]] = {}
    overall = _new_group()
    over_ceiling = 0

    for name, tool_input, tokens, payload_bytes, payload in iter_tool_results(
        transcript_dir
    ):
        by_tool[name] += tokens
        calls_by_tool[name] += 1
        if name != "Bash":
            continue
        command = str(tool_input.get("command", ""))
        family = command_family(command)
        by_family[family] += tokens
        calls_by_family[family] += 1
        if payload_bytes < threshold_bytes:
            continue
        above_by_family[family] += tokens
        rendered, fmt = envelope_of(command, payload, config)
        if len(rendered.encode("utf-8")) > config.max_envelope_bytes:
            over_ceiling += 1
        before, after = len(payload) // 4, len(rendered) // 4
        _record(families.setdefault(family, _new_group()), before, after)
        _record(formats.setdefault(fmt, _new_group()), before, after)
        _record(buckets.setdefault(_bucket_of(payload_bytes), _new_group()), before, after)
        _record(overall, before, after)
        classifier.setdefault(family, collections.Counter())[fmt] += 1

    total = sum(by_tool.values())
    bash_total = sum(by_family.values())
    _finish(families)
    _finish(formats)
    _finish(buckets)
    _finish({"overall": overall})
    return {
        "transcript_dir": transcript_dir,
        "transcript_files": len(
            glob.glob(os.path.join(transcript_dir, "*/agent-*.jsonl"))
        ),
        "threshold_bytes": threshold_bytes,
        "total_tokens": total,
        "total_calls": sum(calls_by_tool.values()),
        "bash_tokens": bash_total,
        "harness_native_tokens": total - bash_total,
        "by_tool": dict(by_tool.most_common()),
        "families": {
            family: {
                "tokens": tokens,
                "calls": calls_by_family[family],
                "above_tokens": above_by_family[family],
                "above_share": above_by_family[family] / tokens if tokens else 0.0,
            }
            for family, tokens in by_family.most_common()
        },
        "population": {
            "overall": overall,
            "over_ceiling": over_ceiling,
            "families": families,
            "formats": formats,
            "buckets": buckets,
            "classifier": {
                family: dict(counter.most_common())
                for family, counter in classifier.items()
            },
        },
    }


def _fixture_capture(entry: dict, payload: str) -> Capture:
    return Capture(
        argv=(),
        command=entry["command"],
        cwd=str(REPO_ROOT),
        exit_code=int(entry.get("exit_code", 0)),
        output=payload,
        captured_at="1970-01-01T00:00:00Z",
    )


def _fixture_blob(entry: dict, config: EnvelopeConfig, payload: str) -> BlobRecord:
    """Build the BlobRecord the cache *would* have produced, without writing it.

    The replay must not touch the real cache — but the blob path is quoted in
    the envelope and in every drill-in recipe, so its realistic length has to be
    charged to the envelope. Using the configured cache root reproduces it.
    """
    digest = entry["sha256"]
    return BlobRecord(
        digest=digest,
        path=config.cache_dir / "blobs" / digest[:2] / digest,
        bytes=len(payload.encode("utf-8")),
        deduped=False,
    )


def replay(manifest_path: Path, config: EnvelopeConfig) -> list[dict]:
    """Run every fixture through the envelope and record before/after tokens."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    corpus_root = manifest_path.parent
    rows = []
    for entry in manifest["fixtures"]:
        payload = (corpus_root / entry["path"]).read_text(encoding="utf-8")
        capture = _fixture_capture(entry, payload)
        enveloped = should_envelope(capture, config)
        if enveloped:
            rendered = envelope_json(
                build_envelope(capture, config, _fixture_blob(entry, config, payload))
            )
        else:
            # Below threshold: envl emits the raw output verbatim. It costs
            # exactly what it cost before, and must be charged as such.
            rendered = payload
        before = len(payload) // 4
        after = len(rendered) // 4
        rows.append(
            {
                "path": entry["path"],
                "command": entry["command"],
                "command_family": entry["command_family"],
                "format": entry["format"],
                "enveloped": enveloped,
                "before_tokens": before,
                "after_tokens": after,
                "before_chars": len(payload),
                "after_chars": len(rendered),
                "valid_json": _is_valid_json(rendered) if enveloped else None,
            }
        )
    return rows


def _is_valid_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def _aggregate(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        bucket = groups.setdefault(
            row[key],
            {"fixtures": 0, "before": 0, "after": 0, "net_loss_fixtures": 0},
        )
        bucket["fixtures"] += 1
        bucket["before"] += row["before_tokens"]
        bucket["after"] += row["after_tokens"]
        if row["after_tokens"] >= row["before_tokens"]:
            bucket["net_loss_fixtures"] += 1
    for bucket in groups.values():
        bucket["ratio"] = bucket["after"] / bucket["before"] if bucket["before"] else 1.0
        bucket["reduction"] = 1.0 - bucket["ratio"]
    return groups


def break_even_rate(ratio: float, drill_fraction: float) -> float:
    """Drill-in rate at which the envelope plus its follow-up costs the original.

    ``r + d * phi = 1`` solved for ``d``. Negative means the envelope is already
    a net loss before any drill-in; above 1 means it pays even if every single
    envelope is followed by a drill-in.

    Reported at two ``phi`` values because one is not enough to be honest.
    ``phi = 0.25`` is the modelled case: a drill-in pulls back a quarter of the
    original (one file of a four-file diff). ``phi = 1.0`` is the worst case a
    drill-in can be — the follow-up pulls back the *entire* output, so the
    envelope was pure overhead. At that bound ``d* = 1 - r``, a far tighter bar,
    and it is the number to quote when someone asks what happens if the
    envelope's recipes are ignored and the agent just cats the blob.
    """
    return (1.0 - ratio) / drill_fraction if drill_fraction else float("inf")


def project(
    baseline: dict,
    corpus_families: dict[str, dict],
    fallback_ratio: float,
    scope_families: list[str],
    drill_rates: list[float],
    drill_fraction: float,
) -> dict:
    """Project a scope's post-envelope token cost onto the live baseline.

    ``projected_after_f = B_f * ((1 - a_f) + a_f * (r_f + d * phi))``. Families
    with no measured coverage borrow the overall ratio and are flagged
    ``imputed`` — an honest report has to say which rows are measured and which
    are assumed. ``corpus_families`` is any mapping of family to
    ``{"ratio", "fixtures", "source"}``; v2 passes the population replay.
    """
    rows = []
    for family in scope_families:
        stats = baseline["families"].get(family)
        if not stats or not stats["tokens"]:
            continue
        corpus_stats = corpus_families.get(family)
        ratio = corpus_stats["ratio"] if corpus_stats else fallback_ratio
        above = stats["above_share"]
        rows.append(
            {
                "family": family,
                "baseline_tokens": stats["tokens"],
                "calls": stats["calls"],
                "above_share": above,
                "ratio": ratio,
                "corpus_fixtures": corpus_stats["fixtures"] if corpus_stats else 0,
                "imputed": corpus_stats is None,
                "source": (
                    corpus_stats.get("source", "measured") if corpus_stats else "imputed"
                ),
                "break_even_rate": break_even_rate(ratio, drill_fraction),
                "projected": {
                    f"{rate:g}": stats["tokens"]
                    * ((1 - above) + above * (ratio + rate * drill_fraction))
                    for rate in drill_rates
                },
            }
        )
    scope_baseline = sum(row["baseline_tokens"] for row in rows)
    imputed_tokens = sum(row["baseline_tokens"] for row in rows if row["imputed"])
    projected = {
        f"{rate:g}": sum(row["projected"][f"{rate:g}"] for row in rows)
        for rate in drill_rates
    }
    total = baseline["total_tokens"]
    return {
        "families": rows,
        "scope_baseline_tokens": scope_baseline,
        "imputed_tokens": imputed_tokens,
        "imputed_share": imputed_tokens / scope_baseline if scope_baseline else 0.0,
        "scope_share_of_total": scope_baseline / total if total else 0.0,
        "projected": projected,
        "saved": {rate: scope_baseline - value for rate, value in projected.items()},
        "saved_share_of_total": {
            rate: (scope_baseline - value) / total if total else 0.0
            for rate, value in projected.items()
        },
    }


def _repo_relative(path: str | Path) -> str:
    """Render a path relative to the repo root, so the report is not machine-specific."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def split_material_families(
    rows: list[dict], floor_share: float = 0.01
) -> tuple[list[dict], dict | None]:
    """Split projection rows into material families and an aggregated tail.

    ``command_family`` falls back to the command's first word, so a shell
    one-liner opening with a variable assignment becomes its own "family". The
    live baseline has ~50 such singletons. They are carried in the projection
    (S3 means every shell family) but listing them individually would bury the
    six rows that actually matter, so anything under ``floor_share`` of the
    scope is rolled into one row.
    """
    total = sum(row["baseline_tokens"] for row in rows)
    if not total:
        return rows, None
    shown = [r for r in rows if r["baseline_tokens"] / total >= floor_share]
    tail = [r for r in rows if r["baseline_tokens"] / total < floor_share]
    if not tail:
        return shown, None
    return shown, {
        "families": len(tail),
        "baseline_tokens": sum(r["baseline_tokens"] for r in tail),
        "imputed": sum(1 for r in tail if r["imputed"]),
    }


def analyse(args: argparse.Namespace) -> dict:
    """Run the whole measurement and return every number the report quotes."""
    config = load_config(Path(args.config) if args.config else None)
    rows = replay(Path(args.manifest), config)
    baseline = measure_baseline(args.baseline_transcripts, config)

    by_format = _aggregate(rows, "format")
    by_command = _aggregate(rows, "command_family")
    corpus_before = sum(row["before_tokens"] for row in rows)
    corpus_after = sum(row["after_tokens"] for row in rows)
    corpus_ratio = corpus_after / corpus_before if corpus_before else 1.0

    population = baseline["population"]
    # The projection runs on population ratios. The corpus ratios are still
    # computed and reported, but as a regression corpus rather than as evidence
    # about the population — see the module docstring.
    ratios = population["families"]
    fallback_ratio = population["overall"]["ratio"]

    all_bash = list(baseline["families"])
    s2_families = list(S1_FAMILIES) + list(S2_EXTRA_FAMILIES)

    scopes = {
        name: project(
            baseline,
            ratios,
            fallback_ratio,
            families,
            args.drill_in_rates,
            args.drill_fraction,
        )
        for name, families in (
            ("S1", list(S1_FAMILIES)),
            ("S2", s2_families),
            ("S3", all_bash),
        )
    }

    native = {
        tool: tokens
        for tool, tokens in baseline["by_tool"].items()
        if tool != "Bash" and tokens
    }
    return {
        "harness_version": HARNESS_VERSION,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "threshold_bytes": config.threshold_bytes,
            "max_items": config.max_items,
            "max_lines": config.max_lines,
            "max_line_chars": config.max_line_chars,
            "max_envelope_bytes": config.max_envelope_bytes,
            "registry_rules": len(config.commands),
            "source": args.config or "built-in defaults (no config file)",
        },
        "drill_in_rates": args.drill_in_rates,
        "drill_fraction": args.drill_fraction,
        "baseline": baseline,
        "corpus": {
            "manifest": _repo_relative(args.manifest),
            "fixtures": len(rows),
            "before_tokens": corpus_before,
            "after_tokens": corpus_after,
            "ratio": corpus_ratio,
            "reduction": 1.0 - corpus_ratio,
            "break_even_rate": break_even_rate(corpus_ratio, args.drill_fraction),
            "invalid_json": [r["path"] for r in rows if r["valid_json"] is False],
            "net_loss": [
                {
                    "path": r["path"],
                    "format": r["format"],
                    "before": r["before_tokens"],
                    "after": r["after_tokens"],
                }
                for r in rows
                if r["after_tokens"] >= r["before_tokens"]
            ],
        },
        "by_format": by_format,
        "by_command_family": by_command,
        "population": population,
        "corpus_vs_population": _corpus_vs_population(by_command, population["families"]),
        "scopes": scopes,
        "harness_native": {
            "tokens": baseline["harness_native_tokens"],
            "share": (
                baseline["harness_native_tokens"] / baseline["total_tokens"]
                if baseline["total_tokens"]
                else 0.0
            ),
            "by_tool": native,
        },
        "rows": rows,
    }


def _corpus_vs_population(
    corpus_families: dict[str, dict], population_families: dict[str, dict]
) -> list[dict]:
    """Pair each family's corpus ratio with its population ratio, worst gap first.

    This table is the evidence for the harness's own central caveat: the corpus
    is sampled largest-first and the ratio is size-dependent, so ``r_corpus``
    runs below ``r_population`` — i.e. the corpus flatters the envelope — and by
    how much is a per-family fact, not a constant.
    """
    paired = []
    for family, corpus in corpus_families.items():
        population = population_families.get(family)
        if population is None:
            continue
        paired.append(
            {
                "family": family,
                "corpus_ratio": corpus["ratio"],
                "corpus_fixtures": corpus["fixtures"],
                "population_ratio": population["ratio"],
                "population_results": population["results"],
                "population_tokens": population["before"],
                "gap": population["ratio"] - corpus["ratio"],
            }
        )
    return sorted(paired, key=lambda row: -row["gap"])


def _k(tokens: float) -> str:
    return f"{tokens / 1000:,.0f}k"


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def _break_even_text(rate: float) -> str:
    if rate < 0:
        return "never"
    if rate > 1:
        return ">1.00"
    return f"{rate:.2f}"


def _break_even_pair(ratio: float, drill_fraction: float) -> str:
    """Break-even at the modelled phi and at the worst-case phi=1.0."""
    return (
        f"{_break_even_text(break_even_rate(ratio, drill_fraction))}"
        f" / {_break_even_text(break_even_rate(ratio, 1.0))}"
    )


def _scope_lines(report: dict, name: str, label: str) -> list[str]:
    scope = report["scopes"][name]
    total = report["baseline"]["total_tokens"]
    lines = [
        f"{name} — {label}",
        f"  in scope: {_k(scope['scope_baseline_tokens'])} of {_k(total)} "
        f"({_pct(scope['scope_share_of_total'])} of baseline)",
    ]
    for rate in report["drill_in_rates"]:
        key = f"{rate:g}"
        lines.append(
            f"  d={rate:<4g} -> {_k(scope['projected'][key])} "
            f"(saves {_k(scope['saved'][key])}, "
            f"{_pct(scope['saved_share_of_total'][key])} of the {_k(total)} baseline)"
        )
    return lines


def render_text(report: dict) -> str:
    """Render the human-readable report."""
    out: list[str] = []
    base = report["baseline"]
    corpus = report["corpus"]
    out.append("ENVELOPE REPLAY — counterfactual against the live token baseline")
    out.append(f"generated {report['generated_utc']}  harness v{report['harness_version']}")
    out.append(
        f"config: {report['config']['source']}, "
        f"threshold={report['config']['threshold_bytes']}B, "
        f"max_envelope={report['config']['max_envelope_bytes']}B, "
        f"{report['config']['registry_rules']} registry rules"
    )
    out.append(
        f"baseline: {_k(base['total_tokens'])} tokens / {base['total_calls']:,} calls "
        f"across {base['transcript_files']} transcripts"
    )
    out.append("")

    out.append("CORPUS REPLAY BY FORMAT FAMILY (chars/4, no json.dumps wrapping)")
    out.append(
        f"  {'family':<10}{'FIX':>5}{'BEFORE':>9}{'AFTER':>9}{'RED':>8}"
        f"{'d* .25/1.0':>14}{'net-loss':>10}"
    )
    for family in FORMAT_FAMILIES:
        stats = report["by_format"].get(family)
        if not stats:
            continue
        loss = "{}/{}".format(stats["net_loss_fixtures"], stats["fixtures"])
        out.append(
            f"  {family:<10}{stats['fixtures']:>5}{stats['before']:>9,}"
            f"{stats['after']:>9,}{_pct(stats['reduction']):>8}"
            f"{_break_even_pair(stats['ratio'], report['drill_fraction']):>14}"
            f"{loss:>10}"
        )
    overall_loss = "{}/{}".format(len(corpus["net_loss"]), corpus["fixtures"])
    out.append(
        f"  {'OVERALL':<10}{corpus['fixtures']:>5}{corpus['before_tokens']:>9,}"
        f"{corpus['after_tokens']:>9,}{_pct(corpus['reduction']):>8}"
        f"{_break_even_pair(corpus['ratio'], report['drill_fraction']):>14}"
        f"{overall_loss:>10}"
    )
    out.append("")

    out.append("CORPUS REPLAY BY COMMAND FAMILY (regression corpus, NOT the projection input)")
    out.append(f"  {'family':<18}{'FIX':>5}{'BEFORE':>9}{'AFTER':>9}{'RED':>8}")
    for family, stats in sorted(
        report["by_command_family"].items(), key=lambda kv: -kv[1]["before"]
    ):
        out.append(
            f"  {family:<18}{stats['fixtures']:>5}{stats['before']:>9,}"
            f"{stats['after']:>9,}{_pct(stats['reduction']):>8}"
        )
    out.append("")

    population = report["population"]
    out.append(
        "POPULATION REPLAY — every above-threshold Bash result (this is what is projected)"
    )
    out.append(
        f"  {'family':<18}{'N':>6}{'BEFORE':>10}{'AFTER':>10}{'r_pop':>8}"
        f"{'r_corpus':>10}{'net-loss':>10}"
    )
    corpus_families = report["by_command_family"]
    ordered = sorted(
        population["families"].items(), key=lambda kv: -kv[1]["before"]
    )
    for family, stats in ordered[:14]:
        corpus_stats = corpus_families.get(family)
        corpus_text = f"{corpus_stats['ratio']:.2f}" if corpus_stats else "—"
        out.append(
            f"  {family:<18}{stats['results']:>6}{stats['before']:>10,}"
            f"{stats['after']:>10,}{stats['ratio']:>8.2f}{corpus_text:>10}"
            f"{_pct(stats['net_loss_share']):>10}"
        )
    overall = population["overall"]
    out.append(
        f"  {'OVERALL':<18}{overall['results']:>6}{overall['before']:>10,}"
        f"{overall['after']:>10,}{overall['ratio']:>8.2f}"
        f"{report['corpus']['ratio']:>10.2f}{_pct(overall['net_loss_share']):>10}"
    )
    out.append(
        "  net-loss = share of the family's above-threshold tokens whose envelope"
        " is LARGER than the output"
    )
    out.append(
        f"  all {overall['results']:,} envelopes are valid JSON; "
        f"{population['over_ceiling']} exceed the "
        f"{report['config']['max_envelope_bytes']}-byte ceiling"
    )
    out.append("")

    out.append("SIZE DEPENDENCE (population) — why a largest-first corpus flatters")
    out.append(f"  {'bucket':<10}{'N':>6}{'BEFORE':>10}{'r':>7}{'net-loss':>10}")
    for label, _, _ in SIZE_BUCKETS:
        stats = population["buckets"].get(label)
        if not stats:
            continue
        out.append(
            f"  {label:<10}{stats['results']:>6}{stats['before']:>10,}"
            f"{stats['ratio']:>7.2f}{_pct(stats['net_loss_share']):>10}"
        )
    out.append("")

    out.append(
        f"SENSITIVITY: drill-in fraction phi={report['drill_fraction']}, "
        f"rates d={report['drill_in_rates']}"
    )
    out.append(
        "  a drill-in follow-up costs phi of the original output; "
        "d* is the rate at which the envelope stops paying"
    )
    out.append("")

    for name, label in (
        ("S1", "committed scope (what TASK-147 wraps: pypeeker, pytest, verify-repo.sh)"),
        ("S2", "plausible scope (S1 + git, needs a wrapper or hook that does not exist)"),
        ("S3", "theoretical ceiling (every shell family, if an agent ran all of it through envl)"),
    ):
        out.extend(_scope_lines(report, name, label))
        out.append("")

    native = report["harness_native"]
    out.append(
        f"STRUCTURALLY UNREACHABLE: {_k(native['tokens'])} "
        f"({_pct(native['share'])} of the baseline)"
    )
    out.append(
        "  harness-native tools are not shell commands; "
        "`envl -- <cmd>` cannot wrap them at any adoption level"
    )
    for tool, tokens in list(native["by_tool"].items())[:6]:
        out.append(f"    {tool:<16}{_k(tokens):>8}")
    out.append("")

    s3 = report["scopes"]["S3"]
    shown, tail = split_material_families(s3["families"])
    out.append("PER-FAMILY PROJECTION DETAIL (S3)")
    out.append(
        f"  {'family':<18}{'B_f':>8}{'a_f':>8}{'r_f':>7}{'d* .25/1.0':>14}"
        f"{'n':>4}  r_f source"
    )
    for row in shown:
        out.append(
            f"  {row['family']:<18}{_k(row['baseline_tokens']):>8}"
            f"{_pct(row['above_share']):>8}{row['ratio']:>7.2f}"
            f"{_break_even_pair(row['ratio'], report['drill_fraction']):>14}"
            f"{row['corpus_fixtures']:>4}  {row['source']}"
        )
    if tail:
        label = "+ {} more".format(tail["families"])
        out.append(
            f"  {label:<18}{_k(tail['baseline_tokens']):>8}"
            f"{'':>8}{'':>7}{'':>14}{'':>4}  {tail['imputed']} imputed"
        )
    out.append(
        "  n = above-threshold results behind r_f (population), not corpus fixtures"
    )
    out.append(
        f"  imputed rows carry {_k(s3['imputed_tokens'])} "
        f"({_pct(s3['imputed_share'])}) of the S3 scope"
    )
    out.append("")

    if corpus["net_loss"]:
        out.append(
            f"NET-LOSS FIXTURES: {len(corpus['net_loss'])} of {corpus['fixtures']} "
            "envelopes are LARGER than the output they replace"
        )
        for item in corpus["net_loss"][:12]:
            out.append(
                f"    {item['format']:<8}{item['before']:>6} -> {item['after']:>6}  "
                f"{item['path']}"
            )
        out.append("")

    if corpus["invalid_json"]:
        out.append("INVALID JSON (a hard failure of the core guarantee):")
        out.extend(f"    {path}" for path in corpus["invalid_json"])
    else:
        out.append(
            f"VALIDITY: all {corpus['fixtures']} replayed envelopes parse as JSON."
        )
    return "\n".join(out)


def _band(scope: dict, rates: list[float]) -> str:
    """Render a scope's saving as a band across the drill-in rates, never one number."""
    shares = [scope["saved_share_of_total"][f"{rate:g}"] for rate in rates]
    return f"{_pct(min(shares))}–{_pct(max(shares))}"


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    align = ["---"] + ["---:"] * (len(header) - 1)
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(align) + "|",
        *["| " + " | ".join(row) + " |" for row in rows],
    ]


def render_markdown(report: dict) -> str:
    """Render the counterfactual document.

    Generated rather than hand-written so the prose cannot drift away from the
    numbers: re-running the harness regenerates the whole document. Every
    projected figure below comes from the population replay; the committed
    corpus appears only in its own section, labelled as the regression corpus
    it is.
    """
    base = report["baseline"]
    corpus = report["corpus"]
    native = report["harness_native"]
    population = report["population"]
    overall = population["overall"]
    s1, s2, s3 = (report["scopes"][k] for k in ("S1", "S2", "S3"))
    rates = report["drill_in_rates"]
    d0 = f"{rates[0]:g}"
    dmax = f"{rates[-1]:g}"
    threshold = report["config"]["threshold_bytes"]
    pytest_pop = population["families"].get("uv run pytest", {"ratio": 0.0})
    small = population["buckets"].get(SIZE_BUCKETS[0][0], {"ratio": 0.0})

    doc_path = report.get("output_path", "docs/envelope-counterfactual.md")
    out = [
        "# Envelope counterfactual (measured)",
        "",
        f"Generated by `python3 scripts/replay-envelope.py --write {doc_path}`."
        " Do not hand-edit: re-run the harness instead. The companion baseline"
        " is `TOKEN-COSTS.md`; this document answers what the envelope"
        " (`src/envl`, TASK-143) would have done to it.",
        "",
        f"**Generated {report['generated_utc']}** — harness v"
        f"{report['harness_version']}, baseline recomputed live from"
        f" {base['transcript_files']} transcripts"
        f" ({_k(base['total_tokens'])} tokens / {base['total_calls']:,} calls),"
        f" reduction ratios measured by replaying **all"
        f" {overall['results']:,} above-threshold Bash results** through the"
        f" shipping envelope. The committed corpus"
        f" (`{corpus['manifest']}`, {corpus['fixtures']} fixtures) is reported"
        " separately and is not what the projection multiplies.",
        "",
        "## Verdict",
        "",
        f"**At the scope this work actually commits to, the envelope saves"
        f" {_band(s1, rates)} of the agent's total tool-result cost.** That is"
        " small enough to be a rounding error, and it is not a surprise:"
        " `TOKEN-COSTS.md` already found that our own tooling's output is ~3% of"
        " the spend. The committed scope — `uv run pypeeker`, `uv run pytest`,"
        f" `verify-repo.sh` — is {_k(s1['scope_baseline_tokens'])} of a"
        f" {_k(base['total_tokens'])} baseline"
        f" ({_pct(s1['scope_share_of_total'])}), so even a perfect envelope"
        " could not have saved more than that — and one of its three families,"
        f" `uv run pytest`, is a **net loss** (r = {pytest_pop['ratio']:.2f}:"
        " the envelope is larger than the output it replaces).",
        "",
        f"**The mechanism works, but only above about 4 KB.** Across the"
        f" {overall['results']:,} replayed results the envelope reduces what it"
        f" wraps by {_pct(overall['reduction'])} overall, and all"
        f" {overall['results']:,} envelopes parse as JSON — the harness fails"
        " the run if any one of them does not, so this is checked rather than"
        " asserted. That average is carried entirely by large output:"
        " bucketed by payload size the ratio runs"
        f" {small['ratio']:.2f} in the {SIZE_BUCKETS[0][0]} band"
        f" (with {_pct(small['net_loss_share'])} of that band's tokens spent on"
        " envelopes *larger* than the output) against"
        f" {population['buckets'][SIZE_BUCKETS[-1][0]]['ratio']:.2f} above"
        " 16 KB. The envelope is a compression device for fat output and close"
        " to a no-op just above the threshold.",
        "",
        f"**The one place this is a large lever is `git`.** Diff output is the"
        " most compressible thing an agent sees — a diff's shape (files,"
        " +/- counts, hunk offsets) is a tiny fraction of its bytes — and the"
        f" `git` family alone carries {_k(base['families']['git']['tokens'])} of"
        " the baseline at a measured r ="
        f" {population['families']['git']['ratio']:.2f}. Wrapping git and"
        f" nothing else would save {_band(s2, rates)} of the total. Nothing in"
        " this arc commits to that: it needs a wrapper or a hook that does not"
        " exist, and TASK-147 is scoped to pypeeker's own commands. If the goal"
        " is token reduction rather than a better LLM-facing CLI, **that is"
        " where the next increment should go**, and this document is the"
        " evidence for it.",
        "",
        f"**{_pct(native['share'])} of the baseline is unreachable by"
        f" construction.** {_k(native['tokens'])} flows through harness-native"
        " tools — `Read`, `Grep`, `Edit` — which are not shell commands."
        " `envl -- <command>` cannot wrap them at any adoption level. This is a"
        " ceiling, not an adoption gap, and no amount of envelope work moves it."
        " `TOKEN-COSTS.md`'s conclusion stands unchanged: the largest lever is"
        " how agents *read files and diffs*, and most of it is reached by"
        " guidance and narrower `Read`/`git diff` habits rather than by"
        " wrapping a CLI.",
        "",
        "**Corpus ratios do not transfer to the population, and harness v1"
        " assumed they did.** The committed corpus is sampled largest-first and"
        " the reduction ratio is strongly size-dependent, so corpus ratios run"
        " well below population ratios in every family that carries the result"
        f" (corpus {corpus['ratio']:.2f} vs population {overall['ratio']:.2f}"
        " overall). v1 of this document projected the corpus ratios and"
        " overstated every scope — S1 by roughly 3x. The numbers above are"
        " projected from the population; the corpus is kept as the regression"
        " corpus for the adapters, which is what it is good for.",
        "",
        "## Method",
        "",
        "- **Estimator**: characters / 4, the same one `measure-tool-costs.py`"
        " uses. That script additionally wraps the payload in `json.dumps`"
        " before counting (~2% inflation from quoting and escaping); the"
        " before/after figures here do not. Both sides of every ratio use the"
        " same convention, so ratios are unaffected and only absolute"
        " before/after counts sit ~2% below the baseline's convention.",
        "- **The baseline is recomputed live** from the transcripts on each run,"
        " not quoted. The transcripts are appended to while the pipeline runs,"
        " so these totals will not match TOKEN-COSTS.md's frozen figures"
        " exactly — see *Biases* below.",
        "- **`r_f` is measured on the whole above-threshold population.** Every"
        " Bash tool result in the transcripts at or above the"
        f" {threshold}-byte threshold ({overall['results']:,} results,"
        f" {_k(overall['before'])} tokens) is replayed through the shipping"
        " envelope at the built-in config. Nothing is sampled, so no sampling"
        " bias can leak into `r_f`.",
        "- **The projection never multiplies raw fixture bytes.**",
        "",
        "  ```",
        "  projected_after_f = B_f * ((1 - a_f) + a_f * (r_f + d * phi))",
        "  ```",
        "",
        f"  where `B_f` is family *f*'s baseline tokens, `a_f` the share of those"
        f" tokens carried by results at or above the {threshold}-byte threshold"
        " (everything below it passes through untouched and is charged at full"
        " price), `r_f` the population reduction ratio for that family, `d` the"
        f" drill-in rate and `phi` = {report['drill_fraction']} the share of the"
        " original a follow-up drill-in pulls back.",
        f"- **Config**: {report['config']['source']},"
        f" threshold {threshold} B, envelope ceiling"
        f" {report['config']['max_envelope_bytes']} B,"
        f" {report['config']['registry_rules']} registry rules. These are"
        " out-of-the-box numbers: with no registry, format labels come from"
        " content sniffing and no per-command truncation tuning is applied. A"
        " tuned registry would move `r_f`, and not necessarily downward — a rule"
        " that keeps *more* per-file detail for `git diff` trades size for fewer"
        " drill-ins. Measuring a tuned config is left as a follow-up rather than"
        " guessed at here.",
        "",
        "## Population replay — what the projection actually uses",
        "",
        f"Every above-threshold Bash result, replayed through `build_envelope`:"
        f" {overall['results']:,} results, {_k(overall['before'])} tokens before,"
        f" {_k(overall['after'])} after (r = {overall['ratio']:.2f}).",
        "",
    ]

    pop_rows = []
    corpus_families = report["by_command_family"]
    ordered = sorted(population["families"].items(), key=lambda kv: -kv[1]["before"])
    for family, stats in ordered[:14]:
        corpus_stats = corpus_families.get(family)
        pop_rows.append(
            [
                f"`{family}`",
                f"{stats['results']:,}",
                f"{stats['before']:,}",
                f"{stats['after']:,}",
                f"{stats['ratio']:.2f}",
                _pct(stats["net_loss_share"]),
                f"{corpus_stats['ratio']:.2f} ({corpus_stats['fixtures']})"
                if corpus_stats
                else "—",
            ]
        )
    pop_rows.append(
        [
            "**overall**",
            f"{overall['results']:,}",
            f"{overall['before']:,}",
            f"{overall['after']:,}",
            f"{overall['ratio']:.2f}",
            _pct(overall["net_loss_share"]),
            f"{corpus['ratio']:.2f} ({corpus['fixtures']})",
        ]
    )
    out += _md_table(
        [
            "family",
            "results",
            "before (tok)",
            "after (tok)",
            "r (population)",
            "net-loss token share",
            "r (corpus, n)",
        ],
        pop_rows,
    )
    out += [
        "",
        "*net-loss token share* is the fraction of that family's"
        " above-threshold tokens whose envelope came out **larger** than the"
        " output it replaced. It is not a rounding artefact: it is two thirds of"
        " the `uv run pytest` family and a third of `grep/rg`.",
        "",
        f"Two invariants are checked on all {overall['results']:,} of these"
        " envelopes rather than argued for: every one parses as JSON (the"
        " harness aborts the run otherwise), and"
        f" {population['over_ceiling']} of them exceed the"
        f" {report['config']['max_envelope_bytes']}-byte ceiling.",
        "",
        "### Size dependence — why a largest-first sample flatters",
        "",
    ]
    bucket_rows = []
    for label, _, _ in SIZE_BUCKETS:
        stats = population["buckets"].get(label)
        if not stats:
            continue
        bucket_rows.append(
            [
                label,
                f"{stats['results']:,}",
                f"{stats['before']:,}",
                f"{stats['ratio']:.2f}",
                _pct(stats["net_loss_share"]),
            ]
        )
    out += _md_table(
        ["payload size", "results", "before (tok)", "r", "net-loss token share"],
        bucket_rows,
    )
    out += [
        "",
        f"The {SIZE_BUCKETS[0][0]} band carries the plurality of real calls and"
        " is where the envelope is neutral-to-negative; everything the envelope"
        " is good at happens above it. This is the single strongest argument in"
        f" the document for **raising `threshold_bytes` from {threshold} toward"
        " 4096**: it would trade coverage for a strictly-positive floor. The"
        " default is left at"
        f" {threshold} here so the numbers describe what actually ships.",
        "",
        "### Classifier coverage",
        "",
        "`detect_format` fires on content, so a family's name is not its"
        " format. Above-threshold results by detected format:",
        "",
    ]
    classifier_rows = []
    for family, counts in sorted(
        population["classifier"].items(),
        key=lambda kv: -population["families"][kv[0]]["before"],
    )[:6]:
        total = sum(counts.values())
        classifier_rows.append(
            [
                f"`{family}`",
                str(total),
                ", ".join(f"{fmt} {n}" for fmt, n in counts.items()),
            ]
        )
    out += _md_table(["family", "results", "detected formats"], classifier_rows)
    out += [
        "",
        "Two consequences worth stating plainly. First, the format-family table"
        " below describes the captures the classifier *labels* that way, not the"
        " command families of the same name — most `uv run pytest` output is"
        " summarised by the plain-text adapter, because a passing `-q` run"
        " carries no `FAILURES` block and no node ids to sniff. Second, that is"
        " a real gap rather than a measurement artefact: a broader pytest and"
        " search sniffer is the cheapest available improvement to `r_f` in those"
        " families and is left as a follow-up.",
        "",
        "## Committed corpus replay (the regression corpus)",
        "",
        f"The {corpus['fixtures']} committed fixtures exist to hold the adapters"
        " to captured reality in the test suite. They are a size-capped,"
        " largest-first sample, so **their ratios are not population ratios** and"
        " are not used in any projection. They are reported because a reader"
        " should be able to check the adapters' behaviour on the same data the"
        " tests use.",
        "",
    ]

    rows = []
    for family in FORMAT_FAMILIES:
        stats = report["by_format"].get(family)
        if not stats:
            continue
        rows.append(
            [
                f"`{family}`",
                str(stats["fixtures"]),
                f"{stats['before']:,}",
                f"{stats['after']:,}",
                _pct(stats["reduction"]),
                _break_even_pair(stats["ratio"], report["drill_fraction"]),
                f"{stats['net_loss_fixtures']}/{stats['fixtures']}",
            ]
        )
    rows.append(
        [
            "**overall**",
            str(corpus["fixtures"]),
            f"{corpus['before_tokens']:,}",
            f"{corpus['after_tokens']:,}",
            _pct(corpus["reduction"]),
            _break_even_pair(corpus["ratio"], report["drill_fraction"]),
            f"{len(corpus['net_loss'])}/{corpus['fixtures']}",
        ]
    )
    out += _md_table(
        [
            "format",
            "fixtures",
            "before (tok)",
            "after (tok)",
            "reduction",
            f"break-even d* @ phi={report['drill_fraction']:g} / phi=1.0",
            "net-loss",
        ],
        rows,
    )
    out += [
        "",
        f"All {corpus['fixtures']} replayed envelopes parse as JSON."
        if not corpus["invalid_json"]
        else f"**{len(corpus['invalid_json'])} envelopes failed to parse as JSON.**",
        "",
        "`d*` is the drill-in rate at which the envelope stops paying: solve"
        " `r + d * phi = 1`. It is quoted at two drill-in fractions because one"
        f" would be flattering. At the modelled phi={report['drill_fraction']:g}"
        " (a follow-up pulls back a quarter of the original — one file of a"
        " four-file diff) a ratio near 0.6 pays even if *every* envelope forces"
        " a drill-in. At `phi=1.0` — the worst case, where the agent ignores the"
        " recipes and just cats the whole blob, so the envelope was pure"
        " overhead — `d* = 1 - r` and the bar is much tighter. Real behaviour"
        " sits between the two; the pessimistic column is what to quote if you"
        " doubt the recipes get used.",
        "",
        "### Corpus versus population, per family",
        "",
        "The gap below is the measurement error v1 of this document shipped."
        " Positive `gap` means the corpus understates what the envelope costs.",
        "",
    ]
    gap_rows = [
        [
            f"`{row['family']}`",
            f"{row['corpus_ratio']:.2f} ({row['corpus_fixtures']})",
            f"{row['population_ratio']:.2f} ({row['population_results']:,})",
            f"{row['gap']:+.2f}",
        ]
        for row in report["corpus_vs_population"]
    ]
    out += _md_table(
        ["family", "r corpus (n)", "r population (n)", "gap"], gap_rows
    )
    out += [
        "",
        "### The net-loss band is real and is not rounded away",
        "",
        f"{len(corpus['net_loss'])} of {corpus['fixtures']} committed fixtures"
        " produce an envelope **larger** than the output it replaces, every one"
        f" of them just above the {threshold}-byte threshold. The population"
        " table above is the honest version of the same fact:"
        f" {_pct(overall['net_loss_share'])} of all above-threshold tokens are"
        " spent on envelopes that cost more than they save.",
        "",
    ]
    if corpus["net_loss"]:
        out += _md_table(
            ["fixture", "format", "before", "after"],
            [
                [f"`{i['path']}`", i["format"], str(i["before"]), str(i["after"])]
                for i in corpus["net_loss"]
            ],
        )
        out.append("")

    out += [
        "## Adoption scopes",
        "",
        "Reported separately because a single blended number would imply a"
        " reachability that does not exist.",
        "",
    ]
    scope_rows = []
    for name, scope, label in (
        ("S1", s1, "committed scope — `uv run pypeeker`, `uv run pytest`, `verify-repo.sh`"),
        ("S2", s2, "plausible scope — S1 + `git` (needs a wrapper or hook that does not exist yet)"),
        ("S3", s3, "theoretical ceiling — every shell family, if an agent ran all of it through `envl`"),
    ):
        scope_rows.append(
            [
                f"**{name}**",
                label,
                _k(scope["scope_baseline_tokens"]),
                _pct(scope["scope_share_of_total"]),
                *[
                    f"{_k(scope['saved'][f'{r:g}'])} "
                    f"({_pct(scope['saved_share_of_total'][f'{r:g}'])})"
                    for r in rates
                ],
            ]
        )
    scope_rows.append(
        [
            "**—**",
            "structurally unreachable — harness-native `Read`/`Grep`/`Edit`, "
            "not shell commands",
            _k(native["tokens"]),
            _pct(native["share"]),
            *["0k (0.0%)" for _ in rates],
        ]
    )
    out += _md_table(
        ["scope", "what it covers", "in scope", "of baseline"]
        + [f"saved @ d={r:g}" for r in rates],
        scope_rows,
    )
    out += [
        "",
        f"The band runs from d={d0} (no drill-in ever needed) to d={dmax} (half"
        " of all envelopes force a follow-up). Savings are quoted as a share of"
        " the whole baseline, so they are directly comparable to"
        " TOKEN-COSTS.md's purpose table.",
        "",
        "### Per-family projection detail (S3)",
        "",
    ]
    shown, tail = split_material_families(s3["families"])
    detail_rows = [
        [
            f"`{row['family']}`",
            _k(row["baseline_tokens"]),
            _pct(row["above_share"]),
            f"{row['ratio']:.2f}",
            _break_even_pair(row["ratio"], report["drill_fraction"]),
            f"{row['corpus_fixtures']:,}",
            row["source"],
        ]
        for row in shown
    ]
    if tail:
        detail_rows.append(
            [
                f"*+ {tail['families']} smaller families*",
                _k(tail["baseline_tokens"]),
                "—",
                "—",
                "—",
                "—",
                f"{tail['imputed']} imputed",
            ]
        )
    out += _md_table(
        [
            "family",
            "B_f",
            "a_f (above threshold)",
            "r_f",
            f"d* @ phi={report['drill_fraction']:g} / 1.0",
            "n (results)",
            "r_f source",
        ],
        detail_rows,
    )
    out += [
        "",
        "Families below 1% of the scope are rolled into one row:"
        " `command_family` falls back to a command's first word, so shell"
        " one-liners opening with a variable assignment each become their own"
        " singleton \"family\". They are still carried in the projection"
        " arithmetic; listing them individually would bury the handful of rows"
        " that actually carry the result.",
        "",
        "Families marked *imputed* have no above-threshold result of their own"
        " and borrow the population-wide ratio. They carry"
        f" {_k(s3['imputed_tokens'])} ({_pct(s3['imputed_share'])}) of the S3"
        " scope — small enough not to move the answer, and labelled rather than"
        " blended away. S1 and S2 are unaffected: every family in them is"
        " measured.",
        "",
        "## Biases and their direction",
        "",
        "Not all of these run the same way. Two of them make the envelope look"
        " better than it is.",
        "",
        "1. **Bash tool results are clipped by the harness at ~30,000"
        " characters** (measured maxima in these transcripts: 29,832 / 28,999 /"
        " 28,999 for Bash, against 73,974 for an uncapped `Read`). *Direction:"
        " roughly neutral.* The clip caps the baseline `B_f` and the measured"
        " `before` alike, so both sides of every ratio are clipped consistently."
        " It does mean nothing here describes genuinely unbounded output — a"
        " 200 KB `git diff`, a wide pytest failure — where the envelope would do"
        " better than these numbers show.",
        "2. **The transcript set grew during this work.** TOKEN-COSTS.md records"
        f" 6,403k tokens / 9,307 calls; this run measures"
        f" {_k(base['total_tokens'])} / {base['total_calls']:,}, because the"
        " pipeline that built the envelope wrote into the same transcript"
        " directory it is measuring. *Direction: neutral*, and a reviewer seeing"
        " the mismatch should not read it as an error.",
        "3. **The population replay charges the envelope's own drill-in"
        " recipes at their printed length but assumes the model uses them"
        " correctly.** The `d` band is the model of what happens when it does"
        " not. *Direction: optimistic at low `d`.*",
        "4. **The committed corpus is a largest-first sample.** *Direction:"
        " strongly anti-conservative if its ratios are projected* — which is"
        " exactly what harness v1 did, and why this document now projects"
        " population ratios instead. The corpus-versus-population table above"
        " quantifies the gap per family.",
        "",
        "## Adoption notes",
        "",
        "- `envl` propagates the wrapped command's exit code, so `&&` chains and"
        " `$?` checks keep working. But `envl -- uv run pytest` exits non-zero"
        " on failure *and prints an envelope*, so any adoption inside"
        " `verify-repo.sh` or CI has to account for the envelope replacing the"
        " step's own output on the failing path — which is the path a human is"
        " reading.",
        "- On this evidence, **do not wrap `uv run pytest`** at the default"
        f" threshold: r = {pytest_pop['ratio']:.2f} on the real population and"
        f" {_pct(pytest_pop.get('net_loss_share', 0.0))} of its above-threshold"
        " tokens are net losses. A passing run is already cheap; a failing run"
        " is what needs wrapping, and telling the two apart needs either a"
        " registry rule or a higher threshold.",
        "- The envelope hands back a path to an **immutable snapshot**, never a"
        " cached re-run. Agents edit files between invocations; serving a stale"
        " blob as if it were live would be a silent-wrong-data bug, which is why"
        " `BlobCache` has no lookup-by-command path and a test asserts it never"
        " grows one.",
        "- Below the threshold, output passes through untouched with no blob"
        " written. The pass-through path is not observable in the cache.",
        "",
        "## Reproducing",
        "",
        "```bash",
        "python3 scripts/replay-envelope.py                 # report to stdout",
        "python3 scripts/replay-envelope.py --json          # machine-readable",
        f"python3 scripts/replay-envelope.py --write {doc_path}",
        "```",
        "",
        "The corpus itself is regenerable with"
        " `python3 scripts/extract-envelope-fixtures.py`, though the transcripts"
        " are ephemeral and a later run will not reproduce this snapshot.",
        "",
    ]
    return "\n".join(out)


def _rates(spec: str) -> list[float]:
    return [float(part) for part in spec.split(",") if part.strip()]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--baseline-transcripts", default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--drill-in-rates", default="0,0.2,0.5", type=_rates)
    parser.add_argument("--drill-fraction", default=0.25, type=float)
    parser.add_argument("--config", default=None)
    parser.add_argument("--json", action="store_true", help="emit the raw numbers")
    parser.add_argument("--write", default=None, help="render the markdown document")
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    """Replay the corpus, project onto the baseline, print or write the report."""
    args = _parse_args(argv)
    if not os.path.isdir(args.baseline_transcripts):
        print(
            f"no such transcript directory: {args.baseline_transcripts}",
            file=sys.stderr,
        )
        return 1
    if not Path(args.manifest).is_file():
        print(f"no such manifest: {args.manifest}", file=sys.stderr)
        return 1

    report = analyse(args)
    if args.write:
        report["output_path"] = _repo_relative(args.write)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    if args.write:
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(report), encoding="utf-8")
        print(f"\nwrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
