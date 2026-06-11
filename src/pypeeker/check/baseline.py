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
ratchets store their facts under sibling keys in this same file, so
readers/writers here preserve unknown top-level namespaces.

The born-private ratchet (TASK-99) stores its recorded public symbol ids
under a sibling ``"symbols"`` namespace::

    {"violations": {...}, "symbols": ["pkg.mod:name", ...]}

Unlike the violations namespace — written only by explicit ``check
--update-baseline`` runs — the symbols namespace is AUTO-SEEDED: the first
run of the ``born-private`` rule against a project without the namespace
records every current public symbol id and reports nothing, so enabling the
rule on a legacy codebase is silent. See
:mod:`pypeeker.check.builtin.born_private`.
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

_SYMBOLS_KEY = "symbols"

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


# ── "symbols" namespace: the born-private symbol ratchet (TASK-99) ──────────
# Same file, sibling namespace. These helpers mirror load/write_baseline's
# preservation pattern: each writer replaces only its own namespace and
# carries every other top-level key through untouched.


def has_symbol_baseline(path: Path) -> bool:
    """True when ``path`` exists and contains a ``"symbols"`` namespace.

    Distinct from ``load_symbol_baseline(path) == set()``: a project seeded
    when it had no public symbols stores an empty list, which must read as
    "already seeded" — every later public symbol is new — not as "seed me
    again". The born-private rule uses this to decide whether to auto-seed.
    """
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    return isinstance(data, dict) and _SYMBOLS_KEY in data


def load_symbol_baseline(path: Path) -> set[str]:
    """Load recorded public symbol ids from the ``"symbols"`` namespace.

    A missing file or absent namespace is an empty baseline (use
    :func:`has_symbol_baseline` to tell those apart from a seeded-empty one).
    Other top-level namespaces (``"violations"``) are ignored here.
    """
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    raw = data.get(_SYMBOLS_KEY, []) if isinstance(data, dict) else []
    return {str(symbol_id) for symbol_id in raw}


def write_symbol_baseline(path: Path, symbol_ids: set[str]) -> list[str]:
    """Record ``symbol_ids`` under the ``"symbols"`` namespace; return them sorted.

    Replaces the ``"symbols"`` namespace wholesale while preserving every
    other top-level namespace already in the file (notably ``"violations"``,
    owned by :func:`write_baseline` — and vice versa). Output is sorted and
    indented for reviewable diffs.

    Called by the born-private rule itself ONLY to auto-seed on first
    enablement; recording later symbols as accepted-public belongs to the
    ``check --update-baseline`` flow, which triggers a re-seed by clearing
    the namespace first (see :func:`clear_symbol_baseline`).
    """
    recorded = sorted(symbol_ids)
    data: dict = {}
    if path.exists():
        existing = json.loads(path.read_text())
        if isinstance(existing, dict):
            data = existing
    data[_SYMBOLS_KEY] = recorded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return recorded


def clear_symbol_baseline(path: Path) -> None:
    """Drop the ``"symbols"`` namespace so the next born-private run re-seeds it.

    This is how ``check --update-baseline`` re-records the accepted-public
    symbol set (the TASK-99 follow-up): the CLI clears the namespace before
    running the rules, and the born-private run that follows finds no
    namespace and self-seeds via :func:`write_symbol_baseline` with the
    CURRENT public surface — exactly "accept today's public symbols". Every
    other top-level namespace (notably ``"violations"``) is preserved; a
    missing file or namespace is a no-op.
    """
    if not path.exists():
        return
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or _SYMBOLS_KEY not in data:
        return
    del data[_SYMBOLS_KEY]
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


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
