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

* **Two passes per target, over one materialized copy.** The findings pass
  compares what each engine *reports*; the fix pass (phase 4, on whenever the
  manifest names a ``fix-engine``) compares what each proposes to *repair* —
  the ordered fix-id list, each repair's description and violation line, the
  ``skipped_conflicts`` and ``declined`` buckets, and the byte-level edits.
  Neither side mutates the target: the old engine runs ``check --fix --plan``
  and the new one plans into a throwaway transaction store. The old side needs
  two invocations there and cannot be folded into one, because its fix report
  carries no edits (they live in the PENDING transaction) and its
  ``residual_violations`` is an integer count rather than violation lines.

* **...and this repo's own manifest naming no fix engine is the same error.**
  The fix pass is opt-in per manifest, which is what every fabricated
  self-test manifest wants — but on ``scripts/parity-manifest.toml`` the
  opt-in is a single line whose loss would skip the entire write half while
  the run still printed ``PASS`` and exited 0. That is the same
  vacuously-green failure the ``claimed`` guard exists to make unreachable,
  so it gets the same guard, the same scoping to that one file, and the same
  shape of escape hatch (``--allow-no-fix-engine``).

* **Determinism is structural, not incidental.** Findings are parsed into a
  frozen, orderable dataclass and sorted before comparison and before
  rendering; the JSON report carries no timestamps, no durations, and no
  work-directory paths (each engine's raw output is additionally scrubbed of
  the work directory and the repo root, as insurance); the work directory is
  fresh per run. Running this script twice over an unchanged tree must
  produce byte-identical ``--json`` output.

Usage:
    python3 scripts/differential-check.py [--manifest PATH]
        [--old-engine "CMD"] [--new-engine "CMD"] [--fix-engine "CMD"]
        [--target NAME ...]
        [--json] [--work-dir PATH] [--keep-work-dir] [--allow-empty-claim]
        [--allow-no-fix-engine]

With no arguments this reads ``scripts/parity-manifest.toml`` and materializes
targets into a fresh temp directory that it removes on exit. ``--work-dir``
overrides where that scratch goes; it must name a path that does not exist or
an empty directory, and only a directory this run created is ever deleted (a
pre-existing empty one is left standing, minus what the run wrote into it).

Exit codes:
0 = parity holds (or the manifest claims nothing and is not this repo's own);
1 = an undeclared divergence (or a stale ``finding`` divergence) was found on
a claimed rule, or the two engines disagree about a repair (or a stale
``fix-divergence`` was declared); 2 = the harness itself could not complete (bad manifest,
missing ledger anchor, unparseable engine output, an engine that crashed,
``scripts/parity-manifest.toml`` claiming nothing without
``--allow-empty-claim``, or that same manifest naming no ``fix-engine``
without ``--allow-no-fix-engine``).
"""

from __future__ import annotations

import argparse
import dataclasses
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
class FixDivergence:
    """One declared, ledger-backed difference in what the two engines *repair*.

    The fix-level counterpart of :class:`Divergence`. It removes exactly one
    repair, named by its derived ``fix_id``, from the named ``side`` before the
    fix comparison — never a whole bucket, for the same reason a
    ``kind="finding"`` divergence removes exactly one finding.

    There is no ``kind``: at the fix layer a divergence is always "this repair
    is on one side and not the other", because a repair that differs in its
    *content* differs in the edit list too, and an edit-list difference is not
    something a declaration may wave through.
    """

    fix_id: str
    side: str
    ledger: str
    target: str | None = None

    @property
    def rule(self) -> str:
        """The rule this repair belongs to, for :func:`verify_ledger_refs`' message.

        Fork #5 derives every fix id as ``<rule>:<mutation>:<anchor>``, so the
        head segment of a fix id *is* the rule id — the shared ledger check can
        report "divergence for rule '<...>'" over a fix divergence without
        knowing it is looking at one.
        """
        return self.fix_id.split(":", 1)[0]


@dataclass(frozen=True)
class Manifest:
    """The parsed, validated contents of ``parity-manifest.toml``."""

    version: int
    claimed: tuple[str, ...]
    new_engine: tuple[str, ...] | None
    fix_engine: tuple[str, ...] | None
    targets: tuple[Target, ...]
    divergences: tuple[Divergence, ...]
    fix_divergences: tuple[FixDivergence, ...] = ()


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
class FixReport:
    """The ``check --fix`` comparison outcome for one target.

    ``differences`` is the verdict; everything else is the evidence. It holds
    the **first** comparison that disagreed, and only that one — the five
    comparisons run in a deliberate order (which repairs, then their text, then
    the two refusal buckets, then the bytes), each downstream of the last, so a
    single root cause would otherwise render as five paragraphs of consequence.

    ``old_fix_ids``/``new_fix_ids`` are ordered, not sets: the order *is* the
    conflict-resolution order, so comparing the lists grades the de-conflict
    and not merely its outcome.
    """

    old_fix_ids: tuple[str, ...]
    new_fix_ids: tuple[str, ...]
    old_skipped_ids: tuple[str, ...]
    new_skipped_ids: tuple[str, ...]
    old_declined_ids: tuple[str, ...]
    new_declined_ids: tuple[str, ...]
    old_edit_count: int
    new_edit_count: int
    differences: tuple[str, ...]
    applied: tuple[str, ...] = ()
    unused: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetReport:
    """The comparison outcome for one target, across every claimed rule."""

    name: str
    rules: tuple[RuleReport, ...]
    errors: tuple[str, ...]
    fix: FixReport | None = None


def _rule_report_failing(report: RuleReport) -> bool:
    return bool(report.missing or report.extra)


def _is_failing(report: TargetReport) -> bool:
    return (
        bool(report.errors)
        or any(_rule_report_failing(r) for r in report.rules)
        or bool(report.fix and report.fix.differences)
    )


# --------------------------------------------------------------------------
# Manifest parsing (pure)
# --------------------------------------------------------------------------

_TOP_KEYS = {
    "version",
    "claimed",
    "new-engine",
    "fix-engine",
    "target",
    "divergence",
    "fix-divergence",
}
_TARGET_KEYS = {"name", "path"}
_DIVERGENCE_KEYS = {"rule", "kind", "ledger", "target", "side", "path", "line", "message"}
_FIX_DIVERGENCE_KEYS = {"fix_id", "side", "target", "ledger"}
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

    raw_fix_engine = data.get("fix-engine")
    fix_engine: tuple[str, ...] | None = None
    if raw_fix_engine is not None:
        if not isinstance(raw_fix_engine, list) or not raw_fix_engine or not all(
            isinstance(a, str) and a for a in raw_fix_engine
        ):
            raise HarnessError(f"{source}: 'fix-engine' must be a non-empty list of strings")
        fix_engine = tuple(raw_fix_engine)

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

    raw_fix_divergences = data.get("fix-divergence", [])
    fix_divergences: list[FixDivergence] = []
    for entry in raw_fix_divergences:
        unknown = set(entry) - _FIX_DIVERGENCE_KEYS
        if unknown:
            raise HarnessError(
                f"{source}: [[fix-divergence]] has unknown key(s): {sorted(unknown)}"
            )
        fix_id = entry.get("fix_id")
        if not isinstance(fix_id, str) or not fix_id:
            raise HarnessError(f"{source}: a [[fix-divergence]] is missing a non-empty 'fix_id'")
        rule = fix_id.split(":", 1)[0]
        if rule not in claimed:
            raise HarnessError(
                f"{source}: fix divergence {fix_id!r} names rule {rule!r}, which is "
                "not in 'claimed' — a fix id is '<rule>:<mutation>:<anchor>', so its "
                "first segment must be a claimed rule"
            )
        side = entry.get("side")
        if side not in _DIVERGENCE_SIDES:
            raise HarnessError(
                f"{source}: fix divergence {fix_id!r} needs 'side' in "
                f"{sorted(_DIVERGENCE_SIDES)}"
            )
        ledger = entry.get("ledger")
        if not isinstance(ledger, str) or len(ledger) < _MIN_LEDGER_ANCHOR_LEN:
            raise HarnessError(
                f"{source}: fix divergence {fix_id!r} needs a 'ledger' anchor of at "
                f"least {_MIN_LEDGER_ANCHOR_LEN} characters"
            )
        target = entry.get("target")
        if target is not None and (
            not isinstance(target, str) or target not in seen_target_names
        ):
            raise HarnessError(
                f"{source}: fix divergence {fix_id!r} has 'target' {target!r}, "
                "which does not name a declared [[target]]"
            )
        fix_divergences.append(
            FixDivergence(fix_id=fix_id, side=side, ledger=ledger, target=target)
        )

    return Manifest(
        version=version,
        claimed=claimed,
        new_engine=new_engine,
        fix_engine=fix_engine,
        targets=tuple(targets),
        divergences=tuple(divergences),
        fix_divergences=tuple(fix_divergences),
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
# Fix-level normalization (both engines speak JSON here)
# --------------------------------------------------------------------------

_OLD_FIX_KEYS = {"fixes", "skipped_conflicts", "declined", "residual_violations", "tx_id"}
_NEW_FIX_KEYS = {"schema", "fixes", "skipped_conflicts", "declined", "edits"}
_FIX_ENTRY_KEYS = {"fix_id", "description", "violation"}
_DECLINED_KEYS = {"fix_id", "reason", "detail"}
_EDIT_KEYS = {"file", "start", "end", "replacement"}


@dataclass(frozen=True)
class FixPayload:
    """One engine's ``check --fix --plan`` answer, normalized for comparison.

    ``fixes`` and ``edits`` keep their emitted order — for ``fixes`` that order
    is the de-conflict's verdict, and for ``edits`` it is the order the kept
    repairs contributed bytes, so both are compared as sequences.
    ``skipped_conflicts`` and ``declined`` are compared as sets: within a pass
    neither carries an ordering that means anything.

    ``residual_violations`` is deliberately not represented. It is a count
    produced by re-running a whole engine after the fix pass, which is a
    property of the engine rather than of the repair, and the findings half of
    this oracle already grades that engine directly.
    """

    fixes: tuple[tuple[str, str, str], ...]
    skipped: tuple[tuple[str, str, str], ...]
    declined: tuple[tuple[str, str], ...]
    edits: tuple[tuple[str, int, int, str], ...]


def _fix_entries(raw: object, *, label: str, scrub: list[str]) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(raw, list):
        raise HarnessError(f"{label} is not a list: {raw!r}")
    entries = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != _FIX_ENTRY_KEYS:
            raise HarnessError(f"{label}[{i}] has the wrong keys: {entry!r}")
        entries.append(
            (
                _scrub(str(entry["fix_id"]), scrub),
                _scrub(str(entry["description"]), scrub),
                _scrub(str(entry["violation"]), scrub),
            )
        )
    return tuple(entries)


def _declined_entries(raw: object, *, label: str, scrub: list[str]) -> tuple[tuple[str, str], ...]:
    """Normalize a ``declined`` list to ``(fix_id, reason)`` pairs.

    ``detail`` is read and dropped on purpose: it is a planner's human-readable
    prose, the one field in either report that is free to be reworded, and the
    ``reason`` beside it is the stable machine code that says the same thing.
    Requiring the key is what keeps that a decision rather than an omission.
    """
    if not isinstance(raw, list):
        raise HarnessError(f"{label} is not a list: {raw!r}")
    entries = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != _DECLINED_KEYS:
            raise HarnessError(f"{label}[{i}] has the wrong keys: {entry!r}")
        entries.append(
            (_scrub(str(entry["fix_id"]), scrub), _scrub(str(entry["reason"]), scrub))
        )
    return tuple(entries)


def parse_old_fix_output(
    report_text: str, transaction_text: str | None, *, scrub: list[str]
) -> FixPayload:
    """Normalize the frozen ``check --fix --plan`` report plus its transaction.

    Two documents because the frozen report carries no edits: it names the
    repairs and leaves the bytes in the PENDING transaction, which
    ``transactions show <tx_id>`` prints. ``transaction_text`` is ``None`` when
    the run planned nothing (``tx_id: null``), which must then be an empty edit
    list rather than an unread one.
    """
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"old engine 'check --fix' output is not valid JSON ({exc}): {report_text[:200]!r}"
        ) from exc
    if not isinstance(report, dict) or not _OLD_FIX_KEYS <= set(report):
        raise HarnessError(
            f"old engine 'check --fix' report is missing required key(s): "
            f"{sorted(_OLD_FIX_KEYS - set(report if isinstance(report, dict) else {}))}"
        )
    edits: tuple[tuple[str, int, int, str], ...] = ()
    if transaction_text is not None:
        try:
            loaded = json.loads(transaction_text)
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"old engine 'transactions show' output is not valid JSON ({exc})"
            ) from exc
        raw_edits = loaded.get("edits") if isinstance(loaded, dict) else None
        if not isinstance(raw_edits, list):
            raise HarnessError("old engine 'transactions show' output has no 'edits' list")
        edits = tuple(
            (
                _normalize_path(_scrub(str(e["file"]), scrub)),
                int(e["start"]),
                int(e["end"]),
                _scrub(str(e["new"]), scrub),
            )
            for e in raw_edits
        )
    return FixPayload(
        fixes=_fix_entries(report["fixes"], label="old 'fixes'", scrub=scrub),
        skipped=_fix_entries(
            report["skipped_conflicts"], label="old 'skipped_conflicts'", scrub=scrub
        ),
        declined=_declined_entries(report["declined"], label="old 'declined'", scrub=scrub),
        edits=edits,
    )


def parse_new_fix_output(payload_text: str, *, scrub: list[str]) -> FixPayload:
    """Normalize the new engine's one-document fix payload."""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"new fix engine output is not valid JSON ({exc}): {payload_text[:200]!r}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != _NEW_FIX_KEYS:
        got = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise HarnessError(
            f"new fix engine output has the wrong keys (got {got!r}, expected "
            f"{sorted(_NEW_FIX_KEYS)})"
        )
    if payload["schema"] != 1:
        raise HarnessError(
            f"new fix engine output has unsupported schema (got {payload['schema']!r}, expected 1)"
        )
    raw_edits = payload["edits"]
    if not isinstance(raw_edits, list):
        raise HarnessError("new fix engine output has no 'edits' list")
    edits = []
    for i, entry in enumerate(raw_edits):
        if not isinstance(entry, dict) or set(entry) != _EDIT_KEYS:
            raise HarnessError(f"new fix engine edit #{i} has the wrong keys: {entry!r}")
        path = _normalize_path(_scrub(str(entry["file"]), scrub))
        if path.startswith("/"):
            raise HarnessError(f"new fix engine edit #{i} has an absolute path: {path!r}")
        edits.append((path, int(entry["start"]), int(entry["end"]), _scrub(str(entry["replacement"]), scrub)))
    return FixPayload(
        fixes=_fix_entries(payload["fixes"], label="new 'fixes'", scrub=scrub),
        skipped=_fix_entries(
            payload["skipped_conflicts"], label="new 'skipped_conflicts'", scrub=scrub
        ),
        declined=_declined_entries(payload["declined"], label="new 'declined'", scrub=scrub),
        edits=tuple(edits),
    )


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


def _drop_fix(payload: FixPayload, fix_id: str) -> tuple[FixPayload, bool, bool]:
    """Remove the one entry named by ``fix_id``, returning ``(payload, found, landed)``.

    A fix id lives in exactly one of the three buckets, so this searches all
    three and removes the first match. ``landed`` says the entry was in
    ``fixes`` — i.e. the repair contributed bytes on that side, which is what
    makes the edit-list comparison unusable for the target (see
    :func:`compare_fixes`).
    """
    for name in ("fixes", "skipped"):
        items = list(getattr(payload, name))
        match = next((i for i, entry in enumerate(items) if entry[0] == fix_id), None)
        if match is not None:
            del items[match]
            return (
                dataclasses.replace(payload, **{name: tuple(items)}),
                True,
                name == "fixes",
            )
    declined = list(payload.declined)
    match = next((i for i, entry in enumerate(declined) if entry[0] == fix_id), None)
    if match is not None:
        del declined[match]
        return dataclasses.replace(payload, declined=tuple(declined)), True, False
    return payload, False, False


def compare_fixes(
    target_name: str,
    old: FixPayload,
    new: FixPayload,
    fix_divergences: tuple[FixDivergence, ...],
) -> FixReport:
    """Compare what the two engines propose to repair, honoring fix divergences.

    Five comparisons, in this order, stopping at the first that disagrees:

    1. the **ordered** ``fixes`` id list — which repairs landed, in the order
       the byte-range de-conflict put them in;
    2. per fix, its ``description`` and its ``violation`` text — the second
       re-grades the finding's message *and* its confidence tier at the fix
       layer, catching a repair attached to the wrong row;
    3. the ``skipped_conflicts`` set — the conflict losers;
    4. the ``declined`` ``(fix_id, reason)`` set — the planner refusals;
    5. the **ordered** edit tuples, ``(file, start, end, replacement)`` — the
       bytes themselves, which is the only comparison that can catch two
       engines agreeing on a repair's name and disagreeing on its effect.

    Stopping at the first is not laziness: each comparison is downstream of the
    last (a missing repair takes its edits with it), so reporting all five
    would render one root cause as five symptoms.
    """
    applied: list[str] = []
    unused: list[str] = []
    errors: list[str] = []
    edits_comparable = True
    for d in fix_divergences:
        if d.target is not None and d.target != target_name:
            continue
        payload = old if d.side == "old" else new
        payload, found, landed = _drop_fix(payload, d.fix_id)
        if not found:
            unused.append(f"fix:{d.side}:{d.fix_id}")
            errors.append(
                f"stale fix divergence declaration: side={d.side} "
                f"fix_id={d.fix_id} matched nothing"
            )
            continue
        applied.append(f"fix:{d.side}:{d.fix_id}")
        if landed:
            # A declared one-sided repair that LANDED contributed bytes the
            # other side legitimately does not have, so the edit lists cannot
            # be compared as sequences on this target. Recorded rather than
            # silently relaxed.
            edits_comparable = False
        if d.side == "old":
            old = payload
        else:
            new = payload

    old_ids = tuple(entry[0] for entry in old.fixes)
    new_ids = tuple(entry[0] for entry in new.fixes)
    differences = list(errors)
    if not differences and old_ids != new_ids:
        differences.append(f"fixes differ: old={list(old_ids)} new={list(new_ids)}")
    if not differences:
        for old_entry, new_entry in zip(old.fixes, new.fixes):
            if old_entry[1] != new_entry[1]:
                differences.append(
                    f"description differs for {old_entry[0]}: "
                    f"old={old_entry[1]!r} new={new_entry[1]!r}"
                )
                break
            if old_entry[2] != new_entry[2]:
                differences.append(
                    f"violation differs for {old_entry[0]}: "
                    f"old={old_entry[2]!r} new={new_entry[2]!r}"
                )
                break
    if not differences:
        old_skipped = {entry[0] for entry in old.skipped}
        new_skipped = {entry[0] for entry in new.skipped}
        if old_skipped != new_skipped:
            differences.append(
                f"skipped_conflicts differ: old-only={sorted(old_skipped - new_skipped)} "
                f"new-only={sorted(new_skipped - old_skipped)}"
            )
    if not differences:
        old_declined = set(old.declined)
        new_declined = set(new.declined)
        if old_declined != new_declined:
            differences.append(
                f"declined differ: old-only={sorted(old_declined - new_declined)} "
                f"new-only={sorted(new_declined - old_declined)}"
            )
    if not differences and edits_comparable and old.edits != new.edits:
        differences.append(f"edits differ: old={list(old.edits)} new={list(new.edits)}")

    return FixReport(
        old_fix_ids=old_ids,
        new_fix_ids=new_ids,
        old_skipped_ids=tuple(sorted(entry[0] for entry in old.skipped)),
        new_skipped_ids=tuple(sorted(entry[0] for entry in new.skipped)),
        old_declined_ids=tuple(sorted(entry[0] for entry in old.declined)),
        new_declined_ids=tuple(sorted(entry[0] for entry in new.declined)),
        old_edit_count=len(old.edits),
        new_edit_count=len(new.edits),
        differences=tuple(differences),
        applied=tuple(applied),
        unused=tuple(unused),
    )


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
                "fix": None if r.fix is None else {
                    "old_fixes": list(r.fix.old_fix_ids),
                    "new_fixes": list(r.fix.new_fix_ids),
                    "old_skipped_conflicts": list(r.fix.old_skipped_ids),
                    "new_skipped_conflicts": list(r.fix.new_skipped_ids),
                    "old_declined": list(r.fix.old_declined_ids),
                    "new_declined": list(r.fix.new_declined_ids),
                    "old_edit_count": r.fix.old_edit_count,
                    "new_edit_count": r.fix.new_edit_count,
                    "differences": list(r.fix.differences),
                    "applied_divergences": list(r.fix.applied),
                    "unused_divergences": list(r.fix.unused),
                },
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
        if r.fix is not None:
            lines.append(
                f"target={r.name} fix old={len(r.fix.old_fix_ids)} "
                f"new={len(r.fix.new_fix_ids)} "
                f"skipped={len(r.fix.old_skipped_ids)}/{len(r.fix.new_skipped_ids)} "
                f"declined={len(r.fix.old_declined_ids)}/{len(r.fix.new_declined_ids)} "
                f"edits={r.fix.old_edit_count}/{r.fix.new_edit_count}"
            )
            if not r.fix.differences:
                lines.append("  PASS")
            for diff in r.fix.differences:
                lines.append(f"  ! {diff}")
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


def run_old_fix_engine(
    materialized: MaterializedTarget, argv: list[str]
) -> tuple[str, str | None]:
    """Plan the frozen engine's repairs over the already-indexed target.

    ``check --fix --plan --no-refresh``: ``--plan`` writes the combined
    ``check-fix`` transaction PENDING and touches no file, which is what keeps
    the materialized tree byte-identical for the new side; ``--no-refresh``
    reuses the index :func:`run_old_engine` already built. Exit 0 or 1 are both
    fine (``check`` exits 1 whenever residual violations remain to show).

    Returns ``(report_json, transaction_json_or_None)``. The second run is
    genuinely required rather than a convenience: the fix report names the
    repairs but carries no edits, and its ``residual_violations`` is an integer
    count, so there is no way to fold the two into one invocation.
    """
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    result = subprocess.run(
        [*argv, "check", "--fix", "--plan", "--no-refresh"],
        cwd=materialized.dir,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode not in (0, 1):
        raise HarnessError(_engine_failure("old engine 'check --fix'", argv, result))
    try:
        tx_id = json.loads(result.stdout).get("tx_id")
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"old engine 'check --fix' output is not valid JSON ({exc}): "
            f"{result.stdout[:200]!r}"
        ) from exc
    if not tx_id:
        return result.stdout, None
    shown = subprocess.run(
        [*argv, "transactions", "show", str(tx_id)],
        cwd=materialized.dir,
        capture_output=True,
        text=True,
        env=env,
    )
    if shown.returncode != 0:
        raise HarnessError(_engine_failure("old engine 'transactions show'", argv, shown))
    return result.stdout, shown.stdout


def run_new_fix_engine(argv: list[str], target_dir: Path, claimed: tuple[str, ...]) -> str:
    """Run the new engine's fix launcher against the same materialized directory.

    Same seam as :func:`run_new_engine` — resolved target, ``cwd=REPO_ROOT`` —
    and the same reasons. The launcher writes its transaction to a throwaway
    temp store rather than the target's ``.pypeeker/``, which by this point
    already holds the old side's PENDING ``check-fix`` transaction.
    """
    full_argv = [*argv, "--target", str(target_dir.resolve()), "--rules", ",".join(sorted(claimed))]
    result = subprocess.run(full_argv, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise HarnessError(_engine_failure("new fix engine", argv, result))
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
    parser.add_argument(
        "--fix-engine",
        default=None,
        type=shlex.split,
        help=(
            "the new engine's check --fix launcher; overrides the manifest's "
            "'fix-engine'. With neither set the fix pass is skipped entirely"
        ),
    )
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
    parser.add_argument(
        "--allow-no-fix-engine",
        action="store_true",
        help=(
            "permit this repo's own parity manifest to name no fix engine, "
            "skipping the write half entirely. Without it a missing "
            "'fix-engine' in scripts/parity-manifest.toml is exit 2, not a "
            "PASS that graded zero repairs"
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
        ledger_errors += verify_ledger_refs(manifest.fix_divergences, ledger_text)
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
        fix_engine_argv = args.fix_engine or (
            list(manifest.fix_engine) if manifest.fix_engine else None
        )
        if not fix_engine_argv and _is_this_repos_manifest(manifest_path):
            if not args.allow_no_fix_engine:
                raise HarnessError(
                    f"{manifest_path} names no fix engine ('fix-engine' is absent) — "
                    "this is the manifest CI grades pypeeker against, and phase 4 "
                    "has a fix engine, so a missing key means the file lost it or "
                    "it failed to load, not that there is nothing to repair. "
                    "Without it the whole write half is skipped and the run still "
                    "reports PASS, which is exactly the vacuous green the claimed "
                    "guard makes unreachable for the read half; pass "
                    "--allow-no-fix-engine to override"
                )

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
                report = compare(
                    target.name, manifest.claimed, old_findings, new_findings, manifest.divergences
                )
                if fix_engine_argv:
                    # Second pass, over the SAME materialized directory: no
                    # extra indexing, and both sides see the state the findings
                    # pass left. Neither writes a source byte — the old side
                    # runs with --plan, the new side plans into a throwaway
                    # transaction store.
                    old_fix_text, old_tx_text = run_old_fix_engine(materialized, old_engine_argv)
                    new_fix_text = run_new_fix_engine(
                        fix_engine_argv, materialized.dir, manifest.claimed
                    )
                    report = dataclasses.replace(
                        report,
                        fix=compare_fixes(
                            target.name,
                            parse_old_fix_output(old_fix_text, old_tx_text, scrub=scrub),
                            parse_new_fix_output(new_fix_text, scrub=scrub),
                            manifest.fix_divergences,
                        ),
                    )
                reports.append(report)
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
