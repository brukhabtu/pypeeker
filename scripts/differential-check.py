#!/usr/bin/env python3
"""The DSL rewrite's differential oracle: old engine vs. new engine, per rule.

dsl-rewrite.md is normative for the program this script serves — it holds
the decision to rewrite the check layer, the freeze on the old one, and the
"## Divergence ledger" that is the sole sanctioned way to declare a
behavioral difference between the two engines. This is phase 1: the oracle
exists before anything it measures.

Design notes, each answering a "why not simpler" question:

* **The old engine is consumed strictly through its CLI, as text.** `check`
  has no `--json` — only `check --fix` does, and neither `check` nor `--fix`
  is available to add one to, because ``src/pypeeker/check/**`` is frozen for
  the duration of the rewrite (see dsl-rewrite.md -> "The freeze"). This
  script imports nothing from ``pypeeker`` at all, so "never imports check
  internals" is structural, not a review rule to remember.

* **``--strict`` always.** The default display filter hides HEURISTIC/UNKNOWN
  findings, which is exactly the tier a naive port is most likely to get
  wrong (dsl-rewrite.md's own divergence ledger flags a confidence-lowering
  bug as "wrong, not divergent" for this reason). Grading the filtered view
  would silently exempt the hardest tier from the oracle.

* **Each target is graded from a materialized scratch copy**, never the
  repository in place. The old engine reads its rule set from
  ``[tool.pypeeker].rules`` in *cwd*'s ``pyproject.toml``, and a manifest
  entry may claim a rule the target's own config does not gate — copying a
  target's ``src`` into a temp dir with a generated ``pyproject.toml`` naming
  exactly the claimed rules is the only way to grade those. It also means
  this script never writes into the tree it is run from, keeps the one
  stateful rule (``born-private``, which self-seeds a baseline in
  ``.pypeeker/``) naturally reset every run, and produces byte-identical
  output to an in-place run because findings are recorded project-root-
  relative.

* **This repo's own manifest claiming nothing is an error, not a pass.** A
  ``claimed = []`` run materializes nothing and starts neither engine, which
  was the phase-1 acceptance smoke test — trivially green. Phase 3 has begun
  and ``scripts/parity-manifest.toml`` claims real rules, so the only way it
  reaches that state now is by being emptied or failing to load, and a
  silently-vacuous green from the oracle CI grades must be unreachable. When
  the manifest under test resolves to that file the harness exits 2 naming
  ``--allow-empty-claim``, the escape that restores the old behavior. The
  guard is deliberately scoped to that one file: fabricated manifests are how
  the harness tests itself and several of them claim nothing on purpose. CI
  cost still only grows as rules are actually claimed.

* **Determinism is structural, not incidental.** Findings are parsed into a
  frozen, orderable dataclass and sorted before comparison and before
  rendering; the JSON report carries no timestamps, no durations, and no
  work-directory paths (each engine's raw output is additionally scrubbed of
  the work directory and the repo root, as insurance); the work directory is
  fresh per run. Running this script twice over an unchanged tree must
  produce byte-identical ``--json`` output.

Usage:
    python3 scripts/differential-check.py [--manifest PATH]
        [--old-engine "CMD"] [--new-engine "CMD"] [--target NAME ...]
        [--json] [--work-dir PATH] [--keep-work-dir] [--allow-empty-claim]

With no arguments this reads ``scripts/parity-manifest.toml`` and materializes
targets into a fresh temp directory that it removes on exit. ``--work-dir``
overrides where that scratch goes; it must name a path that does not exist or
an empty directory, and only a directory this run created is ever deleted (a
pre-existing empty one is left standing, minus what the run wrote into it).

Exit codes:
0 = parity holds (or the manifest claims nothing and is not this repo's own);
1 = an undeclared divergence (or a stale ``finding`` divergence) was found on
a claimed rule; 2 = the harness itself could not complete (bad manifest,
missing ledger anchor, unparseable engine output, an engine that crashed, or
``scripts/parity-manifest.toml`` claiming nothing without
``--allow-empty-claim``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CONFIDENCE_TIERS: tuple[str, ...] = ("declared", "inferred", "heuristic", "unknown")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "scripts" / "parity-manifest.toml"
LEDGER_DOC = REPO_ROOT / "dsl-rewrite.md"
LEDGER_HEADING = "## Divergence ledger"


class HarnessError(Exception):
    """Something about the manifest, the ledger, or an engine's output is wrong.

    Caught once, at the top of :func:`main`, and reported as a clean
    ``differential-check: ERROR: ...`` line with exit code 2 — distinct from
    exit code 1 (a real, ungraded divergence between the two engines), so a
    human can tell "the port diverged" from "the harness is broken" at a
    glance.
    """


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Finding:
    """One normalized finding, from either engine.

    Field order is the sort order: by rule, then path, then line, then
    confidence, then message — which is what makes every comparison and
    every rendered report deterministic regardless of the order either
    engine happened to emit things in.
    """

    rule: str
    path: str
    line: int
    confidence: str
    message: str


@dataclass(frozen=True)
class Target:
    """One project the harness grades: a name and a path relative to the repo root."""

    name: str
    path: str


@dataclass(frozen=True)
class Divergence:
    """One declared, ledger-backed difference between the two engines.

    ``kind == "message"`` drops ``message`` from the comparison key for
    ``rule`` (no ``side``/``path``/``line``). ``kind == "finding"`` removes
    *exactly one* finding, identified by ``(path, line)`` and optionally
    ``message``, from the named ``side`` before comparison — never the whole
    ``(path, line)`` bucket, so a sanctioned difference cannot absorb an
    undeclared regression that happens to sit on the same line. Two
    sanctioned findings at one location need two declarations; adding
    ``message`` narrows a declaration to the exact wording it sanctions.
    """

    rule: str
    kind: str
    ledger: str
    target: str | None = None
    side: str | None = None
    path: str | None = None
    line: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class Manifest:
    """The parsed, validated contents of ``parity-manifest.toml``."""

    version: int
    claimed: tuple[str, ...]
    new_engine: tuple[str, ...] | None
    targets: tuple[Target, ...]
    divergences: tuple[Divergence, ...]


@dataclass(frozen=True)
class RuleReport:
    """The comparison outcome for one claimed rule on one target."""

    rule: str
    old_count: int
    new_count: int
    missing: tuple[Finding, ...]
    extra: tuple[Finding, ...]
    applied: tuple[str, ...]
    unused: tuple[str, ...]


@dataclass(frozen=True)
class TargetReport:
    """The comparison outcome for one target, across every claimed rule."""

    name: str
    rules: tuple[RuleReport, ...]
    errors: tuple[str, ...]


def _rule_report_failing(report: RuleReport) -> bool:
    return bool(report.missing or report.extra)


def _is_failing(report: TargetReport) -> bool:
    return bool(report.errors) or any(_rule_report_failing(r) for r in report.rules)


# --------------------------------------------------------------------------
# Manifest parsing (pure)
# --------------------------------------------------------------------------

_TOP_KEYS = {"version", "claimed", "new-engine", "target", "divergence"}
_TARGET_KEYS = {"name", "path"}
_DIVERGENCE_KEYS = {"rule", "kind", "ledger", "target", "side", "path", "line", "message"}
_DIVERGENCE_KINDS = {"message", "finding"}
_DIVERGENCE_SIDES = {"old", "new"}
_MIN_LEDGER_ANCHOR_LEN = 8


def parse_manifest(data: dict, *, source: str) -> Manifest:
    """Validate and shape a raw TOML dict into a :class:`Manifest`.

    Pure: no filesystem access. Every rejection names ``source`` so a
    reviewer can find the offending file without re-deriving it, and unknown
    keys are rejected by name rather than silently ignored — the same
    failure mode the old engine has with rule names (a typo produces a
    silent false green), which is exactly what this manifest must not repeat.
    """
    unknown_top = set(data) - _TOP_KEYS
    if unknown_top:
        raise HarnessError(f"{source}: unknown top-level key(s): {sorted(unknown_top)}")

    version = data.get("version")
    if version != 1:
        raise HarnessError(f"{source}: unsupported manifest version {version!r} (expected 1)")

    raw_claimed = data.get("claimed", [])
    if not isinstance(raw_claimed, list) or not all(
        isinstance(r, str) and r for r in raw_claimed
    ):
        raise HarnessError(f"{source}: 'claimed' must be a list of non-empty strings")
    if len(set(raw_claimed)) != len(raw_claimed):
        raise HarnessError(f"{source}: 'claimed' contains a duplicate rule name")
    claimed = tuple(sorted(raw_claimed))

    raw_new_engine = data.get("new-engine")
    new_engine: tuple[str, ...] | None = None
    if raw_new_engine is not None:
        if not isinstance(raw_new_engine, list) or not raw_new_engine or not all(
            isinstance(a, str) and a for a in raw_new_engine
        ):
            raise HarnessError(f"{source}: 'new-engine' must be a non-empty list of strings")
        new_engine = tuple(raw_new_engine)

    raw_targets = data.get("target", [])
    if not raw_targets:
        raise HarnessError(f"{source}: at least one [[target]] is required")
    targets: list[Target] = []
    seen_target_names: set[str] = set()
    for entry in raw_targets:
        unknown = set(entry) - _TARGET_KEYS
        if unknown:
            raise HarnessError(f"{source}: [[target]] has unknown key(s): {sorted(unknown)}")
        name = entry.get("name")
        path = entry.get("path")
        if not isinstance(name, str) or not name:
            raise HarnessError(f"{source}: a [[target]] is missing a non-empty 'name'")
        if name in seen_target_names:
            raise HarnessError(f"{source}: duplicate target name '{name}'")
        if not isinstance(path, str) or not path:
            raise HarnessError(f"{source}: target '{name}' is missing a non-empty 'path'")
        seen_target_names.add(name)
        targets.append(Target(name=name, path=path))

    raw_divergences = data.get("divergence", [])
    divergences: list[Divergence] = []
    for entry in raw_divergences:
        unknown = set(entry) - _DIVERGENCE_KEYS
        if unknown:
            raise HarnessError(f"{source}: [[divergence]] has unknown key(s): {sorted(unknown)}")
        rule = entry.get("rule")
        if not isinstance(rule, str) or rule not in claimed:
            # An emptied `claimed` list trips here (on the first surviving
            # [[divergence]] block) before main()'s own empty-claim guard can
            # run, so this message must name that cause too — otherwise the
            # operator whose manifest lost its claims is pointed at divergence
            # bookkeeping instead of the real problem.
            if not claimed:
                raise HarnessError(
                    f"{source}: divergence rule {rule!r} declared but the "
                    "manifest claims no rules at all — an empty 'claimed' list "
                    "is an error now that phase 3 has begun (pass "
                    "--allow-empty-claim only for harness self-tests)"
                )
            raise HarnessError(
                f"{source}: divergence rule {rule!r} is not in 'claimed' — a "
                "divergence can only be declared for a rule the manifest claims"
            )
        kind = entry.get("kind")
        if kind not in _DIVERGENCE_KINDS:
            raise HarnessError(
                f"{source}: divergence for rule '{rule}' has kind {kind!r}, "
                f"expected one of {sorted(_DIVERGENCE_KINDS)}"
            )
        ledger = entry.get("ledger")
        if not isinstance(ledger, str) or len(ledger) < _MIN_LEDGER_ANCHOR_LEN:
            raise HarnessError(
                f"{source}: divergence for rule '{rule}' needs a 'ledger' anchor "
                f"of at least {_MIN_LEDGER_ANCHOR_LEN} characters"
            )
        target = entry.get("target")
        if target is not None and (
            not isinstance(target, str) or target not in seen_target_names
        ):
            raise HarnessError(
                f"{source}: divergence for rule '{rule}' has 'target' {target!r}, "
                "which does not name a declared [[target]]"
            )
        side = entry.get("side")
        path = entry.get("path")
        line = entry.get("line")
        message = entry.get("message")
        if kind == "finding":
            if side not in _DIVERGENCE_SIDES:
                raise HarnessError(
                    f"{source}: divergence for rule '{rule}' (kind='finding') needs "
                    f"'side' in {sorted(_DIVERGENCE_SIDES)}"
                )
            if not isinstance(path, str) or not path:
                raise HarnessError(
                    f"{source}: divergence for rule '{rule}' (kind='finding') needs "
                    "a non-empty 'path'"
                )
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                raise HarnessError(
                    f"{source}: divergence for rule '{rule}' (kind='finding') needs "
                    "an integer 'line' >= 1"
                )
            if message is not None and not isinstance(message, str):
                raise HarnessError(
                    f"{source}: divergence for rule '{rule}' has a non-string 'message'"
                )
        else:  # kind == "message"
            stray = [
                k
                for k, v in (
                    ("side", side),
                    ("path", path),
                    ("line", line),
                    ("message", message),
                )
                if v is not None
            ]
            if stray:
                raise HarnessError(
                    f"{source}: divergence for rule '{rule}' (kind='message') does "
                    f"not take {stray}"
                )
        divergences.append(
            Divergence(
                rule=rule,
                kind=kind,
                ledger=ledger,
                target=target,
                side=side,
                path=path,
                line=line,
                message=message,
            )
        )

    return Manifest(
        version=version,
        claimed=claimed,
        new_engine=new_engine,
        targets=tuple(targets),
        divergences=tuple(divergences),
    )


# --------------------------------------------------------------------------
# Ledger check
# --------------------------------------------------------------------------


def ledger_section(doc_text: str) -> str:
    """Extract the text of the ``## Divergence ledger`` section.

    Runs from the heading to the next line starting with ``## `` (or EOF).
    """
    idx = doc_text.find(LEDGER_HEADING)
    if idx == -1:
        raise HarnessError(f"{LEDGER_DOC}: missing required heading {LEDGER_HEADING!r}")
    rest = doc_text[idx + len(LEDGER_HEADING) :]
    section_lines: list[str] = []
    for line in rest.splitlines():
        if line.startswith("## "):
            break
        section_lines.append(line)
    return "\n".join(section_lines)


def _squash(text: str) -> str:
    """Collapse every whitespace run to a single space, and strip the ends."""
    return re.sub(r"\s+", " ", text).strip()


def verify_ledger_refs(divergences: tuple[Divergence, ...], ledger_text: str) -> list[str]:
    """Return one error string per divergence whose ``ledger`` anchor is absent.

    Whitespace-normalized substring match, so an anchor may cross a line wrap
    in the source markdown. A divergence cannot be invented in the manifest
    without a real, matching ledger entry.
    """
    squashed_ledger = _squash(ledger_text)
    errors = []
    for d in divergences:
        if _squash(d.ledger) not in squashed_ledger:
            errors.append(
                f"divergence for rule '{d.rule}' has no matching entry in "
                f"{LEDGER_DOC.name}'s {LEDGER_HEADING!r} section (anchor: {d.ledger!r})"
            )
    return errors


# --------------------------------------------------------------------------
# Old-engine (text) normalization
# --------------------------------------------------------------------------

OLD_LINE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+): \[(?P<rule>[^\]]+)\] (?P<message>.*?)"
    r"(?: \[(?P<tier>inferred|heuristic|unknown)\])?$"
)
_DEFAULT_TIER = "declared"


def _normalize_path(path: str) -> str:
    """Render ``path`` as a relative POSIX path with no leading ``./``."""
    return str(PurePosixPath(path.replace("\\", "/")))


def _scrub(text: str, scrub: list[str]) -> str:
    for needle in scrub:
        if needle:
            text = text.replace(needle, "<root>")
    return text


def parse_old_line(line: str) -> Finding:
    """Parse one line of ``pypeeker check --strict`` output into a :class:`Finding`.

    The format is ``Violation.__str__``: ``path:line: [rule] message`` with an
    optional trailing `` [tier]`` marker for non-DECLARED confidence.
    """
    match = OLD_LINE.match(line)
    if not match:
        raise HarnessError(f"unparseable old-engine line: {line!r}")
    tier = match.group("tier") or _DEFAULT_TIER
    return Finding(
        rule=match.group("rule"),
        path=_normalize_path(match.group("path")),
        line=int(match.group("line")),
        confidence=tier,
        message=match.group("message"),
    )


def parse_old_output(text: str, *, claimed: tuple[str, ...], scrub: list[str]) -> list[Finding]:
    """Parse and normalize a whole ``check --strict`` run's stdout."""
    findings = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        finding = parse_old_line(_scrub(raw_line, scrub))
        if finding.rule not in claimed:
            raise HarnessError(
                f"old engine emitted a finding for unclaimed rule {finding.rule!r}: "
                f"{raw_line!r}"
            )
        findings.append(finding)
    return sorted(findings)


# --------------------------------------------------------------------------
# New-engine (JSON) normalization
# --------------------------------------------------------------------------

_REQUIRED_FINDING_KEYS = {"rule", "path", "line", "message", "confidence"}


def parse_new_output(payload_text: str, *, claimed: tuple[str, ...], scrub: list[str]) -> list[Finding]:
    """Parse and normalize the new engine's ``{"schema": 1, "findings": [...]}`` payload."""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"new engine output is not valid JSON ({exc}): {payload_text[:200]!r}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        got = payload.get("schema") if isinstance(payload, dict) else type(payload).__name__
        raise HarnessError(f"new engine output has unsupported schema (got {got!r}, expected 1)")
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise HarnessError("new engine output is missing a 'findings' list")

    findings = []
    for i, entry in enumerate(raw_findings):
        if not isinstance(entry, dict) or set(entry) != _REQUIRED_FINDING_KEYS:
            raise HarnessError(f"new engine finding #{i} has the wrong keys: {entry!r}")
        rule = entry["rule"]
        if not isinstance(rule, str) or rule not in claimed:
            raise HarnessError(f"new engine finding #{i} is for unclaimed rule {rule!r}: {entry!r}")
        confidence = entry["confidence"]
        if confidence not in CONFIDENCE_TIERS:
            raise HarnessError(
                f"new engine finding #{i} has unknown confidence {confidence!r}: {entry!r}"
            )
        line = entry["line"]
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise HarnessError(f"new engine finding #{i} has invalid line {line!r}: {entry!r}")
        raw_path = entry["path"]
        if not isinstance(raw_path, str) or not raw_path:
            raise HarnessError(f"new engine finding #{i} has invalid path {raw_path!r}: {entry!r}")
        scrubbed_path = _scrub(raw_path, scrub)
        if scrubbed_path.startswith("/"):
            raise HarnessError(
                f"new engine finding #{i} has an absolute path {scrubbed_path!r}: {entry!r}"
            )
        message = entry["message"]
        if not isinstance(message, str):
            raise HarnessError(f"new engine finding #{i} has a non-string message: {entry!r}")
        findings.append(
            Finding(
                rule=rule,
                path=_normalize_path(scrubbed_path),
                line=line,
                confidence=confidence,
                message=_scrub(message, scrub),
            )
        )
    return sorted(findings)


# --------------------------------------------------------------------------
# Pure comparison core
# --------------------------------------------------------------------------


def _finding_label(d: Divergence) -> str:
    if d.kind == "finding":
        return f"finding:{d.side}:{d.path}:{d.line}"
    return f"message:{d.rule}"


def _diff_by_key(old: list[Finding], new: list[Finding], key) -> tuple[list[Finding], list[Finding]]:
    """Multiset-diff two finding lists by ``key``, returning ``(missing, extra)``."""
    old_by_key: dict[object, list[Finding]] = {}
    for f in old:
        old_by_key.setdefault(key(f), []).append(f)
    new_by_key: dict[object, list[Finding]] = {}
    for f in new:
        new_by_key.setdefault(key(f), []).append(f)

    missing: list[Finding] = []
    for k, items in old_by_key.items():
        excess = len(items) - len(new_by_key.get(k, []))
        if excess > 0:
            missing.extend(items[:excess])
    extra: list[Finding] = []
    for k, items in new_by_key.items():
        excess = len(items) - len(old_by_key.get(k, []))
        if excess > 0:
            extra.extend(items[:excess])
    return sorted(missing), sorted(extra)


def compare(
    target_name: str,
    claimed: tuple[str, ...],
    old: list[Finding],
    new: list[Finding],
    divergences: tuple[Divergence, ...],
) -> TargetReport:
    """Compare old-vs-new findings for every claimed rule, honoring divergences."""
    rule_reports: list[RuleReport] = []
    errors: list[str] = []

    for rule in sorted(claimed):
        rule_divergences = [
            d for d in divergences if d.rule == rule and (d.target is None or d.target == target_name)
        ]
        old_slice = [f for f in old if f.rule == rule]
        new_slice = [f for f in new if f.rule == rule]

        applied: list[str] = []
        unused: list[str] = []

        for d in rule_divergences:
            if d.kind != "finding":
                continue
            side_list = old_slice if d.side == "old" else new_slice

            def _matches(f: Finding, d: Divergence = d) -> bool:
                if f.path != d.path or f.line != d.line:
                    return False
                return d.message is None or f.message == d.message

            # Exactly one finding is removed per declaration, even when several
            # match — a declaration says "this one difference is sanctioned",
            # not "everything at this line is". Removing the whole (path, line)
            # bucket would let one ledger-backed entry swallow an undeclared
            # co-located regression and exit green, and co-located findings are
            # ordinary (unused-imports fires per name on ``import os, sys``;
            # import-boundaries fires per imported symbol). Two sanctioned
            # findings on one line therefore need two ledger entries. The victim
            # is the sort-least match so the choice is deterministic regardless
            # of the order the engine emitted things in.
            matches = sorted(f for f in side_list if _matches(f))
            label = _finding_label(d)
            if matches:
                kept = list(side_list)
                kept.remove(matches[0])
                applied.append(label)
            else:
                kept = list(side_list)
                unused.append(label)
                errors.append(
                    f"stale divergence declaration: rule={rule} kind=finding "
                    f"side={d.side} path={d.path} line={d.line} matched nothing"
                )
            if d.side == "old":
                old_slice = kept
            else:
                new_slice = kept

        message_divergences = [d for d in rule_divergences if d.kind == "message"]
        if message_divergences:
            short_key = lambda f: (f.path, f.line, f.confidence)  # noqa: E731
            old_by_short: dict[object, set[str]] = {}
            for f in old_slice:
                old_by_short.setdefault(short_key(f), set()).add(f.message)
            new_by_short: dict[object, set[str]] = {}
            for f in new_slice:
                new_by_short.setdefault(short_key(f), set()).add(f.message)
            used = any(
                old_by_short[k] != new_by_short.get(k, set()) for k in old_by_short
            )
            for d in message_divergences:
                (applied if used else unused).append(_finding_label(d))
            key_fn = short_key
        else:
            key_fn = lambda f: (f.path, f.line, f.confidence, f.message)  # noqa: E731

        missing, extra = _diff_by_key(old_slice, new_slice, key_fn)
        rule_reports.append(
            RuleReport(
                rule=rule,
                old_count=len(old_slice),
                new_count=len(new_slice),
                missing=tuple(missing),
                extra=tuple(extra),
                applied=tuple(applied),
                unused=tuple(unused),
            )
        )

    return TargetReport(name=target_name, rules=tuple(rule_reports), errors=tuple(errors))


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------


def _finding_to_dict(f: Finding) -> dict:
    return {"rule": f.rule, "path": f.path, "line": f.line, "confidence": f.confidence, "message": f.message}


def report_to_json(reports: list[TargetReport], claimed: tuple[str, ...]) -> dict:
    """Render every target's comparison as one deterministic, timestamp-free dict."""
    return {
        "schema": 1,
        "status": "fail" if any(_is_failing(r) for r in reports) else "pass",
        "claimed": sorted(claimed),
        "targets": [
            {
                "name": r.name,
                "rules": [
                    {
                        "rule": rr.rule,
                        "old_count": rr.old_count,
                        "new_count": rr.new_count,
                        "missing": [_finding_to_dict(f) for f in rr.missing],
                        "extra": [_finding_to_dict(f) for f in rr.extra],
                        "applied_divergences": list(rr.applied),
                        "unused_divergences": list(rr.unused),
                    }
                    for rr in r.rules
                ],
                "errors": list(r.errors),
            }
            for r in sorted(reports, key=lambda t: t.name)
        ],
    }


def _render_finding(f: Finding) -> str:
    marker = "" if f.confidence == _DEFAULT_TIER else f" [{f.confidence}]"
    return f"{f.path}:{f.line}: [{f.rule}] {f.message}{marker}"


def format_report(reports: list[TargetReport]) -> str:
    """Render the human-readable text report."""
    lines: list[str] = []
    for r in sorted(reports, key=lambda t: t.name):
        for rr in r.rules:
            lines.append(
                f"target={r.name} rule={rr.rule} old={rr.old_count} new={rr.new_count} "
                f"missing={len(rr.missing)} extra={len(rr.extra)}"
            )
            if not rr.missing and not rr.extra:
                lines.append("  PASS")
            for f in rr.missing:
                lines.append(f"  - old only: {_render_finding(f)}")
            for f in rr.extra:
                lines.append(f"  + new only: {_render_finding(f)}")
        for err in r.errors:
            lines.append(f"target={r.name} ERROR: {err}")
    overall = "FAIL" if any(_is_failing(r) for r in reports) else "PASS"
    lines.append(f"differential-check: {overall}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Minimal TOML writer, for the materialized target's generated pyproject.toml
# --------------------------------------------------------------------------

_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    return key if _BARE_TOML_KEY.match(key) else json.dumps(key)


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise HarnessError(f"cannot render {value!r} ({type(value).__name__}) as a TOML scalar")


def _toml_value(value: object) -> str:
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                raise HarnessError("cannot render a dict nested inside a TOML list")
            items.append(_toml_scalar(item))
        return "[" + ", ".join(items) + "]"
    return _toml_scalar(value)


def _render_table(path: list[str], table: dict) -> list[str]:
    lines = [f"[{'.'.join(_toml_key(p) for p in path)}]"]
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
    subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
    for key, value in scalars.items():
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in subtables.items():
        lines.append("")
        lines.extend(_render_table([*path, key], value))
    return lines


def render_config_toml(section: dict) -> str:
    """Render ``section`` as a ``[tool.pypeeker]`` TOML document.

    Scalars first, then each nested dict as a ``[tool.pypeeker.<key>]``
    sub-table (TOML requires scalars to precede sub-tables). Supports
    ``str``/``bool``/``int``/``float`` and flat lists of those; a dict nested
    inside a list raises, since TOML cannot express it and silently dropping
    it would change what the copied rule sees.
    """
    lines = ["[tool.pypeeker]"]
    scalars = {k: v for k, v in section.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in section.items() if isinstance(v, dict)}
    for key, value in scalars.items():
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in tables.items():
        lines.append("")
        lines.extend(_render_table(["tool", "pypeeker", key], value))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# IO shell: materialize a target, run each engine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterializedTarget:
    """Where a target's copy landed, and which ``src`` entries it has."""

    dir: Path
    src_entries: tuple[str, ...]


def default_old_engine() -> list[str]:
    """Resolve the old engine's argv, preferring the current interpreter's sibling."""
    sibling = Path(sys.executable).parent / "pypeeker"
    if sibling.exists():
        return [str(sibling)]
    which = shutil.which("pypeeker")
    if which:
        return [which]
    return ["uv", "run", "--project", str(REPO_ROOT), "pypeeker"]


def materialize_target(target: Target, claimed: tuple[str, ...], work_dir: Path) -> MaterializedTarget:
    """Copy ``target``'s ``src`` entries into ``work_dir`` with a generated pyproject.toml.

    Refuses (raises :class:`HarnessError`) if the target has no
    ``[tool.pypeeker]`` section — the harness never forces a project that
    lacks pypeeker config onto the oracle.
    """
    target_root = (REPO_ROOT / target.path).resolve()
    pyproject_path = target_root / "pyproject.toml"
    if not pyproject_path.is_file():
        raise HarnessError(
            f"target '{target.name}' has no pyproject.toml at {pyproject_path} "
            "— refusing to force a target without pypeeker config"
        )
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    section = (data.get("tool") or {}).get("pypeeker")
    if not section:
        raise HarnessError(
            f"target '{target.name}' has no [tool.pypeeker] section in "
            f"{pyproject_path} — refusing to force a target without pypeeker config"
        )
    src_entries = tuple(section.get("src") or ["src"])
    dest_dir = work_dir / target.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_entries:
        src_path = target_root / entry
        if not src_path.is_dir():
            raise HarnessError(
                f"target '{target.name}' src entry '{entry}' is not a directory: {src_path}"
            )
        shutil.copytree(
            src_path,
            dest_dir / entry,
            ignore=shutil.ignore_patterns("__pycache__", ".pypeeker", ".semantic-tool", "*.pyc"),
        )
    new_section = {**section, "rules": list(sorted(claimed))}
    (dest_dir / "pyproject.toml").write_text(render_config_toml(new_section), encoding="utf-8")
    return MaterializedTarget(dir=dest_dir, src_entries=src_entries)


def _engine_failure(label: str, argv: list[str], result: subprocess.CompletedProcess) -> str:
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
    return f"{label} failed (argv={argv!r}, exit={result.returncode}):\n{tail}"


def run_old_engine(materialized: MaterializedTarget, argv: list[str]) -> str:
    """Index then check (``--strict --no-refresh``) the materialized target.

    Requires the index run to exit 0 and the check run to exit in ``{0, 1}``
    (``check`` exits 1 whenever it has findings to show); anything else is a
    harness error carrying the command and the last 40 lines of output.
    """
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    for entry in materialized.src_entries:
        result = subprocess.run(
            [*argv, "index", entry], cwd=materialized.dir, capture_output=True, text=True, env=env
        )
        if result.returncode != 0:
            raise HarnessError(_engine_failure("old engine 'index'", argv, result))
    result = subprocess.run(
        [*argv, "check", "--strict", "--no-refresh"],
        cwd=materialized.dir,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode not in (0, 1):
        raise HarnessError(_engine_failure("old engine 'check'", argv, result))
    return result.stdout


def run_new_engine(argv: list[str], target_dir: Path, claimed: tuple[str, ...]) -> str:
    """Run the new engine against the same materialized directory the old engine used.

    It may reuse ``.pypeeker/`` and the generated ``[tool.pypeeker.<rule>]``
    option tables — both are already sitting in ``target_dir``.

    Runs with ``cwd=REPO_ROOT`` so that a relative launcher path in the
    manifest works from anywhere — matching how :func:`default_old_engine`
    already anchors the old side to the repo root. Because that moves the new
    engine's cwd, the target is resolved *here*, in the harness's own cwd,
    before it is handed over: a relative path would otherwise mean one
    directory to the old side (which inherits the harness cwd) and another to
    the new side, and the new side would read an index that isn't there.
    ``_open_work_root`` already resolves; this is the seam that has to hold the
    invariant, so it enforces it rather than assuming it.
    """
    target = str(target_dir.resolve())
    full_argv = [*argv, "--target", target, "--rules", ",".join(sorted(claimed))]
    result = subprocess.run(full_argv, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise HarnessError(_engine_failure("new engine", argv, result))
    return result.stdout


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _open_work_root(raw: str | None) -> tuple[Path, bool]:
    """Resolve the scratch root, returning ``(path, created_by_this_run)``.

    This harness only ever deletes what it created. With no ``--work-dir`` it
    makes its own temp directory and removes it whole on exit. A
    ``--work-dir`` that does not exist is created here, and is likewise the
    harness's to remove. A ``--work-dir`` that already exists must be an
    *empty* directory — anything else is refused rather than emptied, because
    the flag names scratch space to write into and must never be read as
    permission to erase a directory the user already had (``--work-dir .``
    would otherwise take the working tree with it).

    The returned path is always absolute. A relative ``--work-dir`` is resolved
    against this process's cwd, which is the only interpretation that holds for
    both engines: the old side inherits the harness's cwd, the new side runs
    with ``cwd=REPO_ROOT``, and an unresolved relative path would name two
    different directories to them.
    """
    if raw is None:
        return Path(tempfile.mkdtemp(prefix="differential-check-")).resolve(), True
    work_root = Path(raw).resolve()
    if work_root.exists():
        if not work_root.is_dir():
            raise HarnessError(f"--work-dir exists and is not a directory: {work_root}")
        existing = sorted(p.name for p in work_root.iterdir())
        if existing:
            raise HarnessError(
                f"--work-dir {work_root} already exists and is not empty "
                f"(e.g. {existing[0]!r}) — the harness will not write into or "
                "delete a directory it did not create; pass a nonexistent or "
                "empty path, or omit --work-dir for a temp directory"
            )
        return work_root, False
    work_root.mkdir(parents=True)
    return work_root, True


def _clean_work_root(work_root: Path, *, owned: bool, subdirs: list[Path]) -> None:
    """Remove this run's scratch output, and nothing the run did not create."""
    if owned:
        shutil.rmtree(work_root, ignore_errors=True)
        return
    # Borrowed (pre-existing, verified empty): take back only what was written
    # into it, leaving the directory itself as it was found — which also makes
    # repeated runs against the same --work-dir work.
    for subdir in subdirs:
        shutil.rmtree(subdir, ignore_errors=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--old-engine", default=None, type=shlex.split)
    parser.add_argument("--new-engine", default=None, type=shlex.split)
    parser.add_argument("--target", action="append", default=None, help="repeatable; filters targets")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    parser.add_argument(
        "--work-dir",
        default=None,
        help=(
            "scratch directory for materialized targets; must not exist or must "
            "be empty. Only a directory this run creates is deleted on exit "
            "(default: a fresh temp directory)"
        ),
    )
    parser.add_argument(
        "--keep-work-dir", action="store_true", help="leave this run's scratch output in place"
    )
    parser.add_argument(
        "--allow-empty-claim",
        action="store_true",
        help=(
            "permit this repo's own parity manifest to claim nothing, restoring "
            "the phase-1 trivial pass (no materialization, neither engine runs). "
            "Without it an empty 'claimed' list in scripts/parity-manifest.toml "
            "is exit 2, not a PASS"
        ),
    )
    return parser.parse_args(argv)


def _is_this_repos_manifest(manifest_path: Path) -> bool:
    """True when this run grades pypeeker's own ``scripts/parity-manifest.toml``.

    The empty-claim guard is scoped to that one file rather than to every
    manifest: fabricated manifests are how the harness tests itself, and
    several of those legitimately claim nothing. The failure mode phase 3
    actually has to make unreachable is *this* repo's oracle reporting PASS
    because its manifest lost its claims — which is exactly the run CI and
    ``scripts/verify-repo.sh`` make, both with no ``--manifest`` argument.
    Resolved on both sides so naming the file explicitly is still guarded.
    """
    try:
        return manifest_path.resolve() == DEFAULT_MANIFEST.resolve()
    except OSError:  # pragma: no cover - unresolvable path is not our manifest
        return False


def main(argv: list[str] | None = None) -> int:
    """Load the manifest, verify the ledger, compare each target, report, exit."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            raise HarnessError(f"no such manifest: {manifest_path}")
        with manifest_path.open("rb") as fh:
            data = tomllib.load(fh)
        manifest = parse_manifest(data, source=str(manifest_path))

        ledger_text = ledger_section(LEDGER_DOC.read_text(encoding="utf-8"))
        ledger_errors = verify_ledger_refs(manifest.divergences, ledger_text)
        if ledger_errors:
            raise HarnessError("; ".join(ledger_errors))

        targets = manifest.targets
        if args.target:
            known = {t.name for t in targets}
            unknown = [n for n in args.target if n not in known]
            if unknown:
                raise HarnessError(f"unknown --target: {', '.join(unknown)}")
            targets = tuple(t for t in targets if t.name in args.target)

        if not manifest.claimed and _is_this_repos_manifest(manifest_path):
            if not args.allow_empty_claim:
                raise HarnessError(
                    f"{manifest_path} claims no rules (claimed = []) — this is the "
                    "manifest CI grades pypeeker against, and phase 3 has claimed "
                    "rules, so an empty list means the file was emptied or its "
                    "'claimed' key failed to load, not that parity holds. A "
                    "vacuously green oracle run on this repo's own manifest is "
                    "unreachable by design; pass --allow-empty-claim to override"
                )

        if not manifest.claimed:
            # Zero claimed rules: no materialization, no subprocess of either
            # engine. This is the trivially-green path a synthetic manifest
            # takes; on this repo's own manifest it is reachable only behind
            # --allow-empty-claim (see the guard above).
            if args.json:
                print(json.dumps(report_to_json([], manifest.claimed), indent=2, sort_keys=True))
            else:
                print("differential-check: PASS (0 claimed rules; nothing to compare)")
            return 0

        new_engine_argv = args.new_engine or (
            list(manifest.new_engine) if manifest.new_engine else None
        )
        if not new_engine_argv:
            raise HarnessError(
                "no new-engine command: set 'new-engine' in the manifest or pass --new-engine"
            )
        old_engine_argv = args.old_engine or default_old_engine()

        work_root, harness_owns_work_root = _open_work_root(args.work_dir)
        written: list[Path] = []
        try:
            reports: list[TargetReport] = []
            for target in targets:
                written.append(work_root / target.name)
                materialized = materialize_target(target, manifest.claimed, work_root)
                old_text = run_old_engine(materialized, old_engine_argv)
                new_text = run_new_engine(new_engine_argv, materialized.dir, manifest.claimed)
                scrub = [str(work_root), str(REPO_ROOT)]
                old_findings = parse_old_output(old_text, claimed=manifest.claimed, scrub=scrub)
                new_findings = parse_new_output(new_text, claimed=manifest.claimed, scrub=scrub)
                reports.append(
                    compare(target.name, manifest.claimed, old_findings, new_findings, manifest.divergences)
                )
        finally:
            if not args.keep_work_dir:
                _clean_work_root(work_root, owned=harness_owns_work_root, subdirs=written)

        if args.json:
            print(json.dumps(report_to_json(reports, manifest.claimed), indent=2, sort_keys=True))
        else:
            print(format_report(reports))
        return 1 if any(_is_failing(r) for r in reports) else 0
    except HarnessError as exc:
        print(f"differential-check: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
