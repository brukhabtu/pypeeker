"""Reach: how far out of one file an expression has to look. Derived, never declared.

``dsl-rewrite.md``'s panel convergences include "scope derived from the
expression, never declared". The projection verb in this DSL is already called
``project``, and the word *scope* is taken by
:class:`~pypeeker.models.Scope`, so this axis is named **reach**.

Reach is a two-point join semilattice, ``FILE`` below ``PROJECT``: an
expression that only reads facts recorded in one
:class:`~pypeeker.models.FileIndex` reaches ``FILE``; the moment any part of
it consults the cross-module resolver — a ``follow`` step that crosses files,
or an opaque declaring a ``project:`` read — the whole expression reaches
``PROJECT``. There is no setter and no constructor argument anywhere in the
package that lets a caller assert a reach: :func:`join` over the parts is the
only way one is ever produced.

Reach is load-bearing rather than decorative. :func:`pypeeker.dsl.trait`
refuses to register a ``PROJECT``-reach expression, because the trait
registry's provider signature — ``(FileIndex, symbol_id) -> Trait`` — has no
project substrate to offer it. That is architecture.md's trait-promotion rule
clause (b) turned from a review convention into a runtime check.
"""

from __future__ import annotations

from enum import Enum


class Reach(Enum):
    """How far out of a single file an expression reads.

    ``FILE`` — every fact comes from one already-loaded ``FileIndex``.
    ``PROJECT`` — some part consults the cross-module resolver.
    """

    FILE = "file"
    PROJECT = "project"


def join(*reaches: Reach) -> Reach:
    """Return the least reach that covers every argument.

    ``PROJECT`` absorbs; the empty join is ``FILE`` (an expression reading
    nothing needs nothing beyond the current file).
    """
    return Reach.PROJECT if any(r is Reach.PROJECT for r in reaches) else Reach.FILE
