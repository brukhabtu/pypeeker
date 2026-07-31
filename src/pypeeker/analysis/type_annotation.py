"""The ``type-annotation`` trait: a symbol's recorded type annotation, if any.

The second proven trait pair (TASK-128), alongside
:mod:`pypeeker.analysis.variable_mutation`. It migrates the fact that
``check.rules.prefer_tuple`` and ``refactor.preconditions.InferredListBinding``
both derived independently from ``Symbol.type_annotation`` — the same
three-clause predicate (``raw == "list"`` and ``confidence is INFERRED``)
written verbatim on both sides of the ``check``/``refactor`` boundary:

* :func:`pypeeker.check.rules.prefer_tuple` — a rule, ∀ over candidate
  ``VARIABLE`` symbols: find every one still bound to an inferred list
  literal.
* :class:`pypeeker.refactor.preconditions.InferredListBinding` — a
  precondition, pointwise on one re-resolved symbol: verify the same fact
  still holds on a freshly reloaded index just before a tuplify plan
  commits.

Unlike :class:`~pypeeker.analysis.variable_mutation.VariableMutation`,
``Trait.value`` here is a bare ``str | None`` — the raw annotation text —
rather than a small record: there is exactly one scalar to carry, and the
second dimension either consumer might want (confidence) already has a
home on :class:`~pypeeker.analysis.traits.Trait` itself, so wrapping it in a
one-field dataclass would add nothing. :func:`is_inferred_list` is the one
place the composite two-clause predicate is written, mirroring
:attr:`~pypeeker.analysis.variable_mutation.VariableMutation.is_mutated` as
a convenience for the consumers that want the union rather than the raw
value/confidence pair.

This trait derives from ``Symbol.type_annotation`` alone — a field already
present on any loaded :class:`~pypeeker.models.FileIndex` — so it reads no
bytes and needs no store. It is simulation-safe by construction: nothing
here should be routed through ``IndexStore.read_file`` or any other store
API — not because layering forbids it (``storage`` is in ``analysis``'s
allow-list), but because this module needs no store input and adding one
would break the ``(FileIndex, symbol_id) -> Trait`` signature the
mechanism is built around.
"""

from __future__ import annotations

from pypeeker.analysis.traits import Trait, register_trait
from pypeeker.models import Confidence, FileIndex

TYPE_ANNOTATION = "type-annotation"


@register_trait(TYPE_ANNOTATION)
def _type_annotation(file_index: FileIndex, symbol_id: str) -> Trait:
    """Derive the recorded type annotation for ``symbol_id`` from ``file_index``.

    ``value`` is the annotation's raw text (``ann.raw``), or ``None`` when
    ``symbol_id`` has no annotation or is absent from ``file_index``
    entirely — a missing symbol never raises, it just yields a trait that
    makes every downstream predicate false. ``confidence`` is the
    annotation's own :class:`~pypeeker.models.Confidence` (``DECLARED`` for
    an explicit annotation, ``INFERRED`` for one the binder inferred from
    the assignment's right-hand side) — or ``Confidence.UNKNOWN`` when there
    is no annotation to have a confidence about.
    """
    symbol = next((s for s in file_index.symbols if s.symbol_id == symbol_id), None)
    ann = symbol.type_annotation if symbol is not None else None

    return Trait(
        value=ann.raw if ann is not None else None,
        confidence=ann.confidence if ann is not None else Confidence.UNKNOWN,
        provenance=(
            f"pypeeker.analysis.type_annotation: recorded type annotation of "
            f"'{symbol_id}' in {file_index.file_path}"
        ),
    )


def is_inferred_list(trait: Trait) -> bool:
    """True if ``trait`` records a list literal whose type was inferred, not declared.

    The one place the composite predicate both ``prefer_tuple`` and
    ``InferredListBinding`` need is written: ``raw == "list"`` at
    ``Confidence.INFERRED`` — an explicit ``x: list`` annotation is
    ``DECLARED`` and deliberately not a match.
    """
    return trait.value == "list" and trait.confidence is Confidence.INFERRED
