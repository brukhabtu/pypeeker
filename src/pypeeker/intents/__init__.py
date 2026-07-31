"""The shared change vocabulary: Intent, Footprint, Effect.

``intents`` is a near-leaf package: it depends only on ``models``, ``query``,
and ``storage``, and is importable by both ``check`` and ``refactor`` — the
one place their vocabularies for "what do we want to change" and "what did
changing it do" meet. This is deliberate layering, not an accident of
history: ``refactor`` planners and the batch engine consume these objects,
and ``check`` rules describe the repair they propose as the
:class:`Intent` on ``Violation.remedy`` (TASK-124). **``check`` still never
imports ``refactor``** — rules say *what* should change, only planners in
``refactor`` know *how*.

:class:`Footprint` and :class:`Effect` are the conflict/remap algebra a
batch scheduler runs intents through (see :mod:`pypeeker.intents.footprint`
for the full grammar); :class:`Intent` and its concrete subclasses declare
both, plus how to remap themselves through another intent's effect (see
:mod:`pypeeker.intents.intents`).
"""

from pypeeker.intents.anchors import Anchor, RangeAnchor, SymbolAnchor
from pypeeker.intents.footprint import (
    EMPTY_EFFECT,
    EMPTY_FOOTPRINT,
    ConflictKind,
    Effect,
    Footprint,
    affects,
    replace_leaf_name,
)
from pypeeker.intents.intents import (
    ChangeVisibilityIntent,
    DeleteSymbolIntent,
    ExtractMethodIntent,
    ExtractVariableIntent,
    InlineVariableIntent,
    Intent,
    OrphanedIntent,
    OrphanReason,
    RemoveImportIntent,
    RenameDocstringParamIntent,
    RenameIntent,
    ReplaceTextIntent,
    RewriteStarImportIntent,
    TuplifyIntent,
)

__all__ = [
    "EMPTY_EFFECT",
    "EMPTY_FOOTPRINT",
    "Anchor",
    "ChangeVisibilityIntent",
    "ConflictKind",
    "DeleteSymbolIntent",
    "Effect",
    "ExtractMethodIntent",
    "ExtractVariableIntent",
    "Footprint",
    "InlineVariableIntent",
    "Intent",
    "OrphanReason",
    "OrphanedIntent",
    "RangeAnchor",
    "RemoveImportIntent",
    "RenameDocstringParamIntent",
    "RenameIntent",
    "ReplaceTextIntent",
    "RewriteStarImportIntent",
    "SymbolAnchor",
    "TuplifyIntent",
    "affects",
    "replace_leaf_name",
]
