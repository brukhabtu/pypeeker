"""Baseline ("ratchet") support for ``pypeeker check``.

Adopting a rule on a legacy codebase needs "no NEW violations" semantics:
record the current findings once, then fail only when a finding appears that
is not covered by that record. This module owns the finding identity scheme,
the on-disk baseline format, and the delta computation; the app layer wires it
to ``check --baseline`` / ``check --update-baseline``.

It lives in :mod:`pypeeker.storage` rather than beside a rule engine because
both halves of the system need it and neither may import the other: the rule
layer *reads* the born-private ratchet, the app layer *writes* it and owns the
violations namespace, and the file itself is a ``.pypeeker/`` artifact —
which is exactly what ``storage`` owns.

Identity scheme
---------------
Finding line numbers drift with unrelated edits, so identity must be
line-independent. A finding's identity is the string::

    "{rule}::{anchor_id}"

The anchor id is the stable id of the *row* the finding was rendered from —
the symbol, import, scope or file the rule fired on — so identity survives
edits that merely shift code around, and it survives a reworded message. That
is why this key needs no message normalization: the frozen engine keyed on
``rule::file_path::normalized_message`` and had to strip volatile ``(line N)``
fragments out of the message to stay stable, and a rule that reworded its
message invalidated its own baseline entries.

Two findings with the same identity (one rule firing twice on one anchor) are
handled by COUNTING: the baseline stores ``identity -> count`` and a run is
clean when, per identity, ``current_count <= baseline_count``. Pure line drift
therefore never fires, while a genuinely new duplicate does.

Tradeoff (accepted): a baselined finding whose anchor is renamed reads as one
fixed + one new finding. That is acceptable ratchet semantics — the developer
is already touching that code and can fix or re-baseline it.

Storage format
--------------
``.pypeeker/check-baseline.json`` holds a single JSON object with the finding
counts under a ``"violations"`` namespace::

    {"violations": {"<identity>": <count>, ...}}

Keys are sorted and the file is written with stable indentation so baseline
diffs stay reviewable. The top-level object is namespaced deliberately:
ratchets store their facts under sibling keys in this same file, so
readers/writers here preserve unknown top-level namespaces.

The born-private ratchet (TASK-99) stores its recorded public symbol ids under
a sibling ``"symbols"`` namespace::

    {"violations": {...}, "symbols": ["pkg.mod:name", ...]}

Unlike the violations namespace — written only by explicit ``check
--update-baseline`` runs — the symbols namespace is SEEDED: the first run
against a project without the namespace records every current public symbol id
and reports nothing, so enabling the born-private rule on a legacy codebase is
silent.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from pypeeker.storage.index_store import resolve_storage_root

#: Baseline file name, held inside the resolved storage dir.
BASELINE_FILE = "check-baseline.json"

_VIOLATIONS_KEY = "violations"

_SYMBOLS_KEY = "symbols"


class BaselineKeyed(Protocol):
    """A reported row this module can key: it knows its rule and its anchor.

    Structural on purpose. ``storage`` sits below the rule layer and may not
    name its finding type, so the baseline is keyed on the *shape* every
    reported row already has rather than on a concrete class. Any object with
    a ``rule`` and an ``anchor_id`` string satisfies it with no runtime
    coupling in either direction.
    """

    @property
    def rule(self) -> str:
        """Id of the rule that produced the row."""

    @property
    def anchor_id(self) -> str:
        """Stable id of the row the finding was rendered from."""


_ItemT = TypeVar("_ItemT", bound=BaselineKeyed)


def baseline_path(project_root: Path) -> Path:
    """Return the canonical baseline file path for a project root."""
    return resolve_storage_root(project_root) / BASELINE_FILE


def baseline_identity(item: BaselineKeyed) -> str:
    """Line-independent identity string for a reported row.

    ``rule::anchor_id`` — see the module docstring for why the line number and
    the message are deliberately excluded and how duplicates are disambiguated
    by counting rather than by position.
    """
    return f"{item.rule}::{item.anchor_id}"


def _document(path: Path) -> dict:
    """The baseline file's top-level object, or ``{}`` when there is none.

    A missing file and a file whose top level is not a JSON object both read
    as "no namespaces" — the behaviour every reader below relies on. Malformed
    JSON deliberately raises rather than being swallowed: a corrupt baseline is
    a real problem, and silently treating it as empty would re-report every
    baselined finding as new.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_baseline(path: Path) -> dict[str, int]:
    """Load baselined finding counts from ``path``.

    A missing file is an empty baseline (every finding is new) — this makes
    ``check --baseline`` safe to run before any baseline was recorded. Unknown
    top-level namespaces (future ratchets) are ignored here; only the
    ``"violations"`` namespace is read.
    """
    raw = _document(path).get(_VIOLATIONS_KEY, {})
    return {str(identity): int(count) for identity, count in raw.items()}


def write_baseline(path: Path, items: Iterable[BaselineKeyed]) -> dict[str, int]:
    """Record ``items`` as the new baseline at ``path``; return the counts.

    Replaces the ``"violations"`` namespace wholesale — fixed findings
    therefore shrink the baseline — while preserving any other top-level
    namespaces already in the file (notably ``"symbols"``, TASK-99). Output is
    sorted and indented for reviewable diffs.
    """
    counts = dict(sorted(Counter(baseline_identity(item) for item in items).items()))
    data = _document(path)
    data[_VIOLATIONS_KEY] = counts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return counts


# ── "symbols" namespace: the born-private symbol ratchet (TASK-99) ──────────
# Same file, sibling namespace. These helpers mirror load/write_baseline's
# preservation pattern: each writer replaces only its own namespace and
# carries every other top-level key through untouched.


def baseline_namespaces(path: Path) -> frozenset[str]:
    """Every top-level namespace present in the baseline file at ``path``.

    A missing file, or one whose top level is not an object, has no
    namespaces. Callers use this to tell "namespace absent" apart from
    "namespace present but empty", which is a distinction the ratchets depend
    on (see :func:`has_symbol_baseline`).
    """
    return frozenset(_document(path))


def has_symbol_baseline(path: Path) -> bool:
    """True when ``path`` exists and contains a ``"symbols"`` namespace.

    Distinct from ``load_symbol_baseline(path) == set()``: a project seeded
    when it had no public symbols stores an empty list, which must read as
    "already seeded" — every later public symbol is new — not as "seed me
    again". The born-private ratchet uses this to decide whether to seed.
    """
    return _SYMBOLS_KEY in baseline_namespaces(path)


def load_symbol_baseline(path: Path) -> set[str]:
    """Load recorded public symbol ids from the ``"symbols"`` namespace.

    A missing file or absent namespace is an empty baseline (use
    :func:`has_symbol_baseline` to tell those apart from a seeded-empty one).
    Other top-level namespaces (``"violations"``) are ignored here.
    """
    raw = _document(path).get(_SYMBOLS_KEY, [])
    return {str(symbol_id) for symbol_id in raw}


def write_symbol_baseline(path: Path, symbol_ids: set[str]) -> list[str]:
    """Record ``symbol_ids`` under the ``"symbols"`` namespace; return them sorted.

    Replaces the ``"symbols"`` namespace wholesale while preserving every
    other top-level namespace already in the file (notably ``"violations"``,
    owned by :func:`write_baseline` — and vice versa). Output is sorted and
    indented for reviewable diffs.

    Called ONLY to seed the ratchet on first enablement; recording later
    symbols as accepted-public belongs to the ``check --update-baseline``
    flow, which triggers a re-seed by clearing the namespace first (see
    :func:`clear_symbol_baseline`).
    """
    recorded = sorted(symbol_ids)
    data = _document(path)
    data[_SYMBOLS_KEY] = recorded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return recorded


def clear_symbol_baseline(path: Path) -> None:
    """Drop the ``"symbols"`` namespace so the next run re-seeds it.

    This is how ``check --update-baseline`` re-records the accepted-public
    symbol set (the TASK-99 follow-up): the namespace is cleared before the
    rules run, and the born-private seeding that follows finds no namespace
    and records the CURRENT public surface via
    :func:`write_symbol_baseline` — exactly "accept today's public symbols".
    Every other top-level namespace (notably ``"violations"``) is preserved; a
    missing file or namespace is a no-op.
    """
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or _SYMBOLS_KEY not in data:
        return
    del data[_SYMBOLS_KEY]
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def delta(
    items: Sequence[_ItemT], baseline: Mapping[str, int]
) -> tuple[list[_ItemT], list[str]]:
    """Compare current ``items`` against a ``baseline``.

    Returns ``(new, fixed_identities)``:

    - ``new`` — the concrete items exceeding their identity's baseline count,
      in the order they were given. When an identity occurs more often than
      baselined, the surplus is attributed to the LAST occurrences in the
      caller's order — an arbitrary but deterministic choice: earlier
      occurrences are treated as the baselined ones, later ones as the new
      duplicates.
    - ``fixed_identities`` — sorted identities whose current count dropped
      below the baselined count (including identities that vanished
      entirely); ``check --update-baseline`` shrinks the baseline for these.

    **Caller obligation:** pass ``items`` already sorted by
    ``(path, line, rule, message)``. This function deliberately does not sort
    — a reported row is not required to be orderable — so "the surplus is the
    last occurrences" means "last in the order you gave", and it reproduces
    the frozen engine's ``(file, line)``-ordered attribution exactly when, and
    only when, that precondition holds. The run service that produces the
    findings owns that sort.
    """
    occurrences: dict[str, list[int]] = {}
    for position, item in enumerate(items):
        occurrences.setdefault(baseline_identity(item), []).append(position)

    selected: set[int] = set()
    for identity, positions in occurrences.items():
        surplus = len(positions) - baseline.get(identity, 0)
        if surplus > 0:
            selected.update(positions[-surplus:])

    new = [items[position] for position in sorted(selected)]
    fixed_identities = sorted(
        identity
        for identity, count in baseline.items()
        if len(occurrences.get(identity, ())) < count
    )
    return new, fixed_identities
