"""Run one named expression over the symbols universe and shape the answer.

The ``pypeeker query`` workflow minus its Click parsing and printing, kept
beside :mod:`pypeeker.dsl.engine`'s ``_run`` as the second run-and-shape
entry point over a corpus: install the builtin expressions, resolve the
name, compose the optional anchor clause, evaluate, and render each match as
the ``{"anchor", "file_path", "line", "confidence"[, "why"]}`` object the
command prints.
"""

from __future__ import annotations

from pypeeker.dsl.anchors import resolve_symbol_anchor
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.expr import row
from pypeeker.dsl.library import expression, install_expressions
from pypeeker.dsl.provenance import derivation_document
from pypeeker.dsl.selection import symbols
from pypeeker.storage import IndexStore

__all__ = ["run_expression"]


def run_expression(
    store: IndexStore,
    src_roots: tuple[str, ...],
    expression_name: str,
    *,
    anchor_id: str | None = None,
    why: bool = False,
) -> dict:
    """Evaluate the builtin expression ``expression_name`` over ``store``.

    Returns ``{"expression", "universe", "reach", "anchor", "results"}``.
    ``reach`` is read off the final selection, so it reflects every clause
    the query carries, the anchor's included; each result carries its anchor
    (with the evidence the anchor stands on), its location and the
    confidence the row matched at, plus a ``why`` derivation document when
    ``why`` is set.

    Raises a :class:`~pypeeker.dsl.errors.DslError` subclass for an unknown
    expression name (checked first: the cheaper and likelier mistake) or an
    anchor that resolves to nothing or to several symbols. Installing the
    expressions is inside that contract too, so a refusal raised while
    registering them surfaces the same way.
    """
    corpus = Corpus(store, src_roots)
    install_expressions()
    expr = expression(expression_name)
    selection = symbols()
    if anchor_id is not None:
        # Written as a clause rather than pushed into row production: there
        # is no optimizer here by design, so narrowing a selection means
        # writing a predicate, and it derives like every other one.
        anchor = resolve_symbol_anchor(corpus, anchor_id)
        selection = selection.where(row.symbol_id.eq(anchor.id))
    # Rebound, not chained inline: "reach" below is read off this selection,
    # and reach is derived from the clauses it carries. A selection missing
    # the expression clause under-reports the query's own cost the moment a
    # PROJECT-reach expression exists.
    selection = selection.where(expr)
    matches = selection.rows(corpus)
    results = []
    for match in matches:
        result = {
            "anchor": {
                "kind": match.anchor.kind.value,
                "id": match.anchor.id,
                "evidence": match.anchor.evidence.value,
            },
            "file_path": match.fields.get("file_path"),
            "line": match.fields.get("line"),
            "confidence": match.confidence.value,
        }
        if why:
            result["why"] = derivation_document(match.derivations)
        results.append(result)
    return {
        "expression": expression_name,
        "universe": selection.universe,
        "reach": selection.reach.value,
        "anchor": anchor_id,
        "results": results,
    }
