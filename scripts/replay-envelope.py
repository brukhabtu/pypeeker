#!/usr/bin/env python3
"""Replay the envelope fixture corpus and project the saving onto the token baseline.

This is the acceptance criterion for the envelope work (TASK-144), and it is
built to be hard to flatter. Three things follow from that:

* **The baseline is recomputed live**, by re-running the same tool_use /
  tool_result pairing that ``.claude/skills/measure-tool-costs/measure-tool-costs.py``
  uses (command families come from ``scripts/claude_transcripts.py``, pinned
  by test to that skill's table),
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

With no arguments it replays the committed corpus against this repository's
most recent workflow transcripts (``~/.claude/projects/<this repo>/<newest
session>/subagents/workflows``) and prints the report to stdout; it exits
non-zero when the repository has no transcripts, since a report over an empty
baseline would be meaningless. ``--write`` renders the same findings as the
markdown counterfactual document. The renderers live in
``scripts/replay_envelope_report.py``.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_transcripts import command_family, default_transcript_dir  # noqa: E402
from envl import (  # noqa: E402
    BlobRecord,
    Capture,
    EnvelopeConfig,
    build_envelope,
    envelope_json,
    load_config,
    should_envelope,
)
from replay_envelope_report import (  # noqa: E402
    SIZE_BUCKETS,
    break_even_rate,
    render_markdown,
    render_text,
)

# Re-exported so tests that load this script by path keep one surface for the
# whole harness (arithmetic and rendering alike).
from replay_envelope_report import (  # noqa: E402, F401
    _band,
    _break_even_text,
    split_material_families,
)

HARNESS_VERSION = 2

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "envelope" / "MANIFEST.json"

# The transcript baseline defaults to this repository's own most recent
# workflow transcripts (see claude_transcripts.default_transcript_dir); ``None``
# means the project has none and a no-argument run must refuse rather than
# measure an empty baseline.
DEFAULT_TRANSCRIPTS = default_transcript_dir(REPO_ROOT)

# Adoption scopes. S1 is what TASK-147 actually commits to wrapping; S2 adds the
# git family, which needs a wrapper or a hook that does not exist yet; S3 is
# every shell family, i.e. the ceiling if an agent ran everything through envl.
S1_FAMILIES = ("uv run pypeeker", "uv run pytest", "verify-repo.sh")
S2_EXTRA_FAMILIES = ("git",)

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
    if args.baseline_transcripts is None:
        print(
            f"no workflow transcripts found for {REPO_ROOT} under ~/.claude/projects; "
            "pass --baseline-transcripts DIR",
            file=sys.stderr,
        )
        return 1
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
