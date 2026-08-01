"""Anchor union: what an intent (or, from TASK-124 stage B, a finding) points at.

Three shapes, all frozen and hashable:

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
* :class:`EdgeAnchor` — a *relationship* anchor: the edge between two symbols
  rather than either endpoint. The unit of work in the importer half of a
  move-symbol is precisely an edge — the ``from <module> import <name>``
  statement binding a definition into another module — so a refusal can name
  *which edge* could not be rewritten instead of merely which file. Added in
  TASK-131 alongside its first consumer,
  :class:`~pypeeker.refactor.move.MoveSymbolPlanner`.

:data:`Anchor` is the union of the three.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pypeeker.intents.footprint import Effect


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


IMPORT_EDGE = "import"
"""The only :attr:`EdgeAnchor.kind` v1 produces (see :class:`EdgeAnchor`)."""


@dataclass(frozen=True)
class EdgeAnchor:
    """Anchors on the relationship between two symbols, not on either endpoint.

    ``source_id`` is the *dependent* end and ``target_id`` the *depended-upon*
    end; ``kind`` names the relationship. Exactly one kind exists today,
    :data:`IMPORT_EDGE` — the edge a ``from <module> import <name>`` statement
    creates between the importing module's local IMPORT binding
    (``source_id``) and the definition it names (``target_id``). No further
    vocabulary is invented ahead of a consumer: an edge kind nothing produces
    is an unread contract.

    Why an edge and not two symbol anchors: a move-symbol rewrites *import
    statements*, and the thing that can fail is one statement, in one file,
    binding one definition. Anchored on the definition alone a refusal can
    only say "some importer"; anchored on the import symbol alone it loses
    what the import was *for*. The edge carries both, which is what lets
    :class:`~pypeeker.refactor.move.MoveSymbolPlanner` refuse by naming the
    exact edge it could not rewrite.
    """

    source_id: str
    target_id: str
    kind: str = IMPORT_EDGE

    def remap(self, effect: "Effect") -> "EdgeAnchor | None":
        """This edge with both endpoints rewritten through ``effect``.

        Each endpoint follows :meth:`~pypeeker.intents.footprint.Effect.remap_id`
        independently (exact substitution or prefix descent), so an edge whose
        target a prior intent renamed — including a *move*, which is a rename
        in id space — points at the new id. ``None`` means the edge is
        **orphaned**: one of its endpoints was deleted, and an edge with a
        missing end is not an edge. A caller reports that with the existing
        :attr:`~pypeeker.intents.intents.OrphanReason.ANCHOR_DELETED` — no new
        enum member, since the reason really is a deleted anchor — and names
        the dead endpoint in the detail, which it recovers by asking
        ``effect.remap_id`` for each endpoint in turn.

        Returns ``self`` when neither endpoint moved (anchors are frozen, so
        sharing is safe).
        """
        source = effect.remap_id(self.source_id)
        target = effect.remap_id(self.target_id)
        if source is None or target is None:
            return None
        if source == self.source_id and target == self.target_id:
            return self
        return EdgeAnchor(source, target, self.kind)


Anchor = SymbolAnchor | RangeAnchor | EdgeAnchor
"""Everything an intent (or, later, a check finding) can point at."""


__all__ = ["IMPORT_EDGE", "Anchor", "EdgeAnchor", "RangeAnchor", "SymbolAnchor"]
