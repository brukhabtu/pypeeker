"""Baseline ("ratchet") support for ``pypeeker check``.

Adopting a rule on a legacy codebase needs "no NEW violations" semantics:
record the current violations once, then fail only when a violation appears
that is not covered by that record. This module owns the violation identity
scheme, the on-disk baseline format, and the delta computation; the CLI wires
it to ``check --baseline`` / ``check --update-baseline``.

Identity scheme
---------------
Violation line numbers drift with unrelated edits, so identity must be
line-independent. A violation's identity is the string::

    "{rule}::{file_path}::{normalized_message}"

where ``normalized_message`` strips volatile ``(line N)`` fragments that some
rules embed in their messages (e.g. purity observation summaries). Messages
otherwise embed stable symbol ids/names, so the identity survives edits that
merely shift code around.

Two violations with the same identity (same rule firing twice with an
identical message in one file) are handled by COUNTING: the baseline stores
``identity -> count`` and a run is clean when, per identity,
``current_count <= baseline_count``. Pure line drift therefore never fires,
while a genuinely new duplicate does.

Tradeoff (accepted): a baselined violation that both moves *and* changes its
message (e.g. the offending symbol is renamed) reads as one fixed + one new
violation. That is acceptable ratchet semantics — the developer is already
touching that code and can fix or re-baseline it.

Storage format
--------------
``.semantic-tool/check-baseline.json`` holds a single JSON object with the
violation counts under a ``"violations"`` namespace::

    {"violations": {"<identity>": <count>, ...}}

Keys are sorted and the file is written with stable indentation so baseline
diffs stay reviewable. The top-level object is namespaced deliberately:
future ratchets (e.g. the born-private symbol ratchet, TASK-99) will store
their own facts under sibling keys in this same file, so readers/writers here
preserve unknown top-level namespaces.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pypeeker.check.models import Violation

#: Project-relative location of the baseline file.
BASELINE_RELPATH = Path(".semantic-tool") / "check-baseline.json"

_VIOLATIONS_KEY = "violations"

#: Volatile message fragment: rules that summarize observations embed
#: ``(line N)`` which shifts with unrelated edits.
_LINE_FRAGMENT = re.compile(r"\s*\(line \d+\)")


def baseline_path(project_root: Path) -> Path:
    """Return the canonical baseline file path for a project root."""
    return project_root / BASELINE_RELPATH


def violation_identity(violation: Violation) -> str:
    """Line-independent identity string for a violation.

    ``rule::file_path::normalized_message`` — see the module docstring for
    why the line number is deliberately excluded and how duplicates are
    disambiguated by counting rather than by position.
    """
    return (
        f"{violation.rule}::{violation.file_path}::"
        f"{_normalize_message(violation.message)}"
    )


def _normalize_message(message: str) -> str:
    """Strip volatile ``(line N)`` fragments so messages survive line drift."""
    return _LINE_FRAGMENT.sub("", message)


def load_baseline(path: Path) -> dict[str, int]:
    """Load baselined violation counts from ``path``.

    A missing file is an empty baseline (every violation is new) — this makes
    ``check --baseline`` safe to run before any baseline was recorded.
    Unknown top-level namespaces (future ratchets) are ignored here; only the
    ``"violations"`` namespace is read.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    raw = data.get(_VIOLATIONS_KEY, {}) if isinstance(data, dict) else {}
    return {str(identity): int(count) for identity, count in raw.items()}


def write_baseline(path: Path, violations: list[Violation]) -> dict[str, int]:
    """Record ``violations`` as the new baseline at ``path``; return the counts.

    Replaces the ``"violations"`` namespace wholesale — fixed violations
    therefore shrink the baseline — while preserving any other top-level
    namespaces already in the file (reserved for future ratchets, TASK-99).
    Output is sorted and indented for reviewable diffs.
    """
    counts = dict(sorted(Counter(violation_identity(v) for v in violations).items()))
    data: dict = {}
    if path.exists():
        existing = json.loads(path.read_text())
        if isinstance(existing, dict):
            data = existing
    data[_VIOLATIONS_KEY] = counts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return counts


def delta(
    violations: list[Violation], baseline: dict[str, int]
) -> tuple[list[Violation], list[str]]:
    """Compare current ``violations`` against a ``baseline``.

    Returns ``(new, fixed_identities)``:

    - ``new`` — the concrete violations exceeding their identity's baseline
      count, sorted. When an identity occurs more often than baselined, the
      surplus is attributed to the LAST occurrences in (file, line) order —
      an arbitrary but deterministic choice: earlier occurrences are treated
      as the baselined ones, later ones as the new duplicates.
    - ``fixed_identities`` — sorted identities whose current count dropped
      below the baselined count (including identities that vanished
      entirely); ``check --update-baseline`` shrinks the baseline for these.
    """
    occurrences: dict[str, list[Violation]] = {}
    for violation in sorted(violations):
        occurrences.setdefault(violation_identity(violation), []).append(violation)

    new: list[Violation] = []
    for identity, found in occurrences.items():
        surplus = len(found) - baseline.get(identity, 0)
        if surplus > 0:
            new.extend(found[-surplus:])
    new.sort()

    fixed_identities = sorted(
        identity
        for identity, count in baseline.items()
        if len(occurrences.get(identity, ())) < count
    )
    return new, fixed_identities
