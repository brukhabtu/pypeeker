"""Anchor union: what an intent (or, from TASK-124 stage B, a finding) points at.

Two shapes today, both frozen and hashable:

* :class:`SymbolAnchor` — a semantic anchor, a symbol id. Survives renames in
  the same batch via :meth:`~pypeeker.intents.footprint.Effect.remap_id`
  (the same substitution :func:`~pypeeker.intents.intents._remap_symbol_anchor`
  already applies for every symbol-anchored intent).
* :class:`RangeAnchor` — a textual anchor: a 0-indexed ``(line, column)``
  position in a file, paired with the anchor text a planner re-verifies
  against current bytes before it edits (the same discipline the
  superseded fix protocol's ``ReplaceTextFix`` used). A range anchor has no id-substitution
  form to follow, so intents built on one remap as the identity — see
  :class:`~pypeeker.intents.intents.ReplaceTextIntent`.

:data:`Anchor` is the union of the two. A third shape, ``EdgeAnchor``
(anchoring a *reference*/call edge rather than a definition or a text range),
is named in the target architecture (see ``architecture.md`` -> "Target
architecture" item 1) but deliberately **not** added here: nothing in the
codebase needs to anchor on an edge yet, and an unconsumed export trips the
``unused-public-symbol`` self-lint gate (see the deferral note this module's
sibling, :mod:`pypeeker.intents.intents`, already carries for `Anchor`
itself). Add it in the stage that first needs it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolAnchor:
    """Anchors on a symbol id — the shape every rename/delete-style intent uses."""

    symbol_id: str


@dataclass(frozen=True)
class RangeAnchor:
    """Anchors on a 0-indexed ``(line, column)`` position inside ``file_path``.

    ``line``/``column`` use the same index conventions as
    :class:`~pypeeker.models.location.Position` (byte column, 0-indexed
    line). Text-anchored planners re-verify the expected text sits here (or
    re-anchor to a unique occurrence elsewhere in the file) before emitting
    an edit — the anchor by itself only names *where the search starts*.
    """

    file_path: str
    line: int
    column: int


Anchor = SymbolAnchor | RangeAnchor
"""Everything an intent (or, later, a check finding) can point at."""


__all__ = ["Anchor", "RangeAnchor", "SymbolAnchor"]
