"""The mutation family: ``no-argument-mutation`` and ``no-hidden-global-mutation``.

Phase 3e of the rewrite recorded in ``dsl-rewrite.md``. Two frozen rules that
share a shape and a substrate: each walks every ``FUNCTION`` / ``METHOD`` symbol
in the project, builds an ``AnalysisContext`` for it, and reports one violation
per **mutation site** inside that function's scope subtree. A mutation site is a
:class:`~pypeeker.models.Reference` — so the port quantifies over the
``references`` universe, and the outer loop becomes a field.

**The pointwise reformulation, stated once for both rules.** The frozen loop
asks, per function, "which references are in my subtree"; the port asks, per
reference, "which function am I in". Those are the same relation read in
opposite directions, and the second is a function rather than a relation: a
reference's set of enclosing functions under the frozen loop is a singleton or
empty. Walk up ``parent_scope_id`` from ``ref.in_scope_id`` and stop at the
first FUNCTION/LAMBDA scope; the answer is the FUNCTION/METHOD symbol that
scope names, or nothing. Class and comprehension scopes are walked through,
matching ``analysis.context._scope_subtree``, which descends into them and
stops only at a nested function or lambda. That walk is published as
``row.enclosing_function_id`` by :func:`pypeeker.dsl.universes._reference_record`
— a general model fact ("which function body is this reference in"), not this
family's private judgement.

One frozen clause is deliberately **not** ported. ``outer_scope_writes`` skips a
write whose target is in ``ctx.local_symbol_ids``; every scope id in a
function's subtree is ``<module>:<local>`` and therefore contains ``":"``, while
shape 1a's surviving clause requires the target's parent scope to contain none,
so the local check can never decide anything the module-scope check has not
already decided. An unreachable clause would claim the rule asks a question it
never asks.

Two aggregation differences between the frozen rules and any ∀-query are
recorded in ``dsl-rewrite.md``'s divergence ledger rather than here: the frozen
``no-hidden-global-mutation`` collects into a ``set``, and the frozen
``no-argument-mutation`` resolves each function id project-wide.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pypeeker.dsl.expr import Expr, any_of, not_, opaque, row
from pypeeker.dsl.selection import Selection, references
from pypeeker.dsl.sweeps import as_str_list, global_rebind_rows, is_module_scope, mutator_names
from pypeeker.models import (
    UNRESOLVED_PREFIX,
    ReferenceKind,
    SymbolKind,
    is_unresolved_attr,
    leaf_name,
    strip_shadow,
    unresolved_attr_name,
)

# Imports are from concrete sibling modules, never from the ``pypeeker.dsl``
# barrel, for the reason :mod:`pypeeker.dsl.sweeps` records: the barrel imports
# this package's modules, so importing it back yields a partially-initialized
# module. The edge to ``sweeps`` is one-way — nothing there imports this module
# — and it exists so the deep import of ``pypeeker.analysis.purity`` (the shared
# collection-mutation table) stays in the one module that documents why it is
# allowed.

_SELF_NAMES = ("self", "cls")
"""Receiver names the frozen rules never treat as an argument mutation.

``check.builtin.no_argument_mutation._SELF_NAMES``. Mutating your own instance
is the job of a method. Reached twice on the parameter side: through
``analysis.calls.classify_receiver``, which maps a ``self``/``cls`` receiver to
``ReceiverKind.SELF`` for the two attribute shapes, and through an explicit
name test for the subscript shape.
"""


# ---------------------------------------------------------------------------
# shared clauses
# ---------------------------------------------------------------------------


def _in_a_function() -> Expr:
    """The frozen outer loop's ``if symbol.kind not in (FUNCTION, METHOD): continue``.

    A reference with no enclosing function is one no frozen ``AnalysisContext``
    subtree contains: a module-level statement, or — the shape that makes this
    a real clause rather than a formality — a mutation inside a **lambda body**,
    which owns no FUNCTION/METHOD symbol for the frozen loop to iterate to.
    """
    return row.enclosing_function_id.is_true()


def allow_by_id(patterns: tuple[str, ...]) -> Expr:
    """``no-argument-mutation``'s ``allow``: an fnmatch against the function's id.

    The frozen rule's ``any(fnmatch.fnmatchcase(symbol.symbol_id, pattern) ...)``,
    written in the grammar rather than smuggled into an opaque —
    :meth:`~pypeeker.dsl.Expr.matches` *is* ``fnmatchcase``.

    With no patterns this is an empty disjunction, which is ``False``, and the
    clause is applied unconditionally for that reason: the frozen rule evaluates
    ``any(...)`` on every function whether or not anything is configured, and a
    selection that skipped the stage would claim it never asked.
    """
    return any_of(*(row.enclosing_function_id.matches(pattern) for pattern in patterns))


def allow_by_id_or_module(patterns: tuple[str, ...]) -> Expr:
    """``no-hidden-global-mutation``'s ``allow``: the id **or** the module path.

    ``check.builtin.no_hidden_global_mutation._matches_any`` tests each pattern
    against ``symbol_id`` and against ``symbol_id.split(":", 1)[0]``, and the
    two clauses are interleaved per pattern here so the written order matches —
    fork #3 makes written order normative even where every clause is pure.

    ``row.enclosing_function_id_module`` **is** that ``split``, computed on the
    row rather than in the clause because the grammar has no string operator for
    it. The same rows carry it under the same name whether they come from the
    references universe or from :func:`pypeeker.dsl.sweeps.global_rebind_rows`,
    which is what lets one builder serve all four parts of the rule.
    """
    return any_of(
        *(
            clause
            for pattern in patterns
            for clause in (
                row.enclosing_function_id.matches(pattern),
                row.enclosing_function_id_module.matches(pattern),
            )
        )
    )


def _leaf_method() -> Expr:
    """``check.builtin.no_argument_mutation._leaf_method``, as a declared-reads opaque.

    **This is not** :func:`pypeeker.models.leaf_name`, and the two are not
    interchangeable. Both take the attribute leaf out of an id, but on an id
    with neither ``"."`` nor ``":"`` — a bare module-level name — ``leaf_name``
    returns the whole id while this returns ``None``. ``no-argument-mutation``
    uses this one and ``no-hidden-global-mutation`` uses ``leaf_name``, so the
    difference is load-bearing: a bare id reaching the mutator table under
    ``leaf_name`` could match a configured ``extra-mutators`` entry that this
    function never lets through.

    Returning ``None`` rather than ``""`` matters for the same clause: the
    ``is_in`` that follows rejects ``None`` outright, whereas a project
    configuring ``extra-mutators = [""]`` would make an empty string match.
    """

    @opaque("mutation-leaf-method", reads=("symbol_id", "is_attribute_access"))
    def _method(record: Any) -> str | None:
        sid = record.symbol_id
        if is_unresolved_attr(sid):
            return unresolved_attr_name(sid)
        if record.is_attribute_access:
            if "." in sid:
                return sid.rsplit(".", 1)[-1]
            if ":" in sid:
                return sid.rsplit(":", 1)[-1]
        return None

    return _method


def _leaf() -> Expr:
    """:func:`pypeeker.models.leaf_name` over the reference's own id.

    The leaf both rules quote when they name an attribute, and the mutator name
    ``no-hidden-global-mutation`` looks up — deliberately a different function
    from :func:`_leaf_method`; see there.
    """

    @opaque("mutation-leaf-name", reads=("symbol_id",))
    def _name(record: Any) -> str:
        return leaf_name(record.symbol_id)

    return _name


def _at_module_scope(field: str) -> Expr:
    """True when the named parent-scope field is a module scope id.

    ``check.builtin.no_hidden_global_mutation._is_module_scope`` — "no ``:``
    segment" — applied to ``binding_parent_scope_id`` in shape 1a and to
    ``receiver_root_parent_scope_id`` in shape 2.

    An opaque rather than ``not_(row.<field>.matches("*:*"))``, because the two
    disagree on the value the field most often carries.
    :meth:`pypeeker.dsl.Expr.matches` is ``False`` for a non-string, so a glob
    against a ``None`` scope id — every reference whose target is not declared
    in this file — is ``False``, and the negation turns that into ``True``,
    admitting exactly the rows the frozen ``target is None: continue`` rejects.
    """

    @opaque("mutation-module-scope", reads=(field,))
    def _module_scope(record: Any) -> bool:
        return is_module_scope(getattr(record, field))

    return _module_scope


# ---------------------------------------------------------------------------
# no-argument-mutation
# ---------------------------------------------------------------------------


def _argument_base(options: Mapping[str, Any]) -> Selection:
    """Every reference inside a function the ``allow`` list does not exempt.

    The frozen outer loop's two filters, in its order and above every shape
    clause: the enclosing symbol must be a FUNCTION/METHOD, and its id must not
    match a configured pattern.
    """
    allow = tuple(as_str_list(options.get("allow")))
    return references().where(_in_a_function()).where(not_(allow_by_id(allow)))


def _parameter_receiver(selection: Selection) -> Selection:
    """``classify_receiver(...) == ReceiverKind.PARAMETER``, as two clauses.

    ``analysis.calls.classify_receiver`` returns ``PARAMETER`` exactly when the
    receiver root resolves — in this file's symbol table — to a ``PARAMETER``
    symbol whose name is neither ``self`` nor ``cls``. Both halves are visible
    clauses rather than one opaque because both are plain field tests, and a
    ``self`` receiver going silent is a documented behaviour of the rule rather
    than an implementation detail of a helper.
    """
    return selection.where(row.receiver_root_kind.eq(SymbolKind.PARAMETER)).where(
        not_(row.receiver_root_name.is_in(*_SELF_NAMES))
    )


def argument_mutator_call(options: Mapping[str, Any]) -> Selection:
    """Shape 1: a collection-mutator call whose receiver root is a parameter.

    ``items.append(x)``, ``cfg.options.update(d)``. The clause order is
    ``_mutation_detail``'s first arm read top to bottom: kind, attribute access,
    the leaf method, its membership in the mutator table, then the receiver.

    ``via`` is the frozen ``".".join([*chain[1:], method])`` — the receiver
    chain **without its root**, which the message already names, plus the method
    — so a single-segment chain words as ``append()`` and a two-segment one as
    ``options.update()``.

    Options (``[tool.pypeeker.no-argument-mutation]``):
        ``allow``          — fnmatch patterns over the function's ``symbol_id``.
        ``extra-mutators`` — method names added to the shared collection-mutator
                             table.
    """
    mutators = tuple(sorted(mutator_names(options)))

    @opaque("mutation-receiver-via", reads=("receiver_chain", "method"))
    def _via(record: Any) -> str:
        return ".".join([*record.receiver_chain[1:], record.method])

    selection = (
        _argument_base(options)
        .where(row.kind.eq(ReferenceKind.CALL))
        .where(row.is_attribute_access.is_true())
        .with_field("method", _leaf_method())
        .where(row.method.is_in(*mutators))
    )
    return _parameter_receiver(selection).with_field("via", _via)


def argument_attribute_write(options: Mapping[str, Any]) -> Selection:
    """Shape 2: an attribute write on a parameter receiver (``obj.x = 1``)."""
    selection = (
        _argument_base(options)
        .where(row.kind.eq(ReferenceKind.WRITE))
        .where(row.is_attribute_access.is_true())
    )
    return _parameter_receiver(selection).with_field("attribute", _leaf())


def argument_subscript_write(options: Mapping[str, Any]) -> Selection:
    """Shape 3: a write whose target *is* a parameter (``xs[0] = 1``, ``x += 1``).

    The binder records a subscript store and an augmented assignment with the
    same WRITE shape on the root symbol, so both arrive here and both are worded
    "subscript write". That coarseness is frozen and documented in the frozen
    rule's own docstring; changing the wording would break parity.

    The ``self``/``cls`` test is explicit here, where the two attribute shapes
    get it from ``classify_receiver``: this shape has no receiver to classify.
    """
    return (
        _argument_base(options)
        .where(row.kind.eq(ReferenceKind.WRITE))
        .where(row.is_attribute_access.eq(False))
        .where(row.binding_kind.eq(SymbolKind.PARAMETER))
        .where(not_(row.binding_name.is_in(*_SELF_NAMES)))
    )


# ---------------------------------------------------------------------------
# no-hidden-global-mutation
# ---------------------------------------------------------------------------


def _global_base(options: Mapping[str, Any]) -> Selection:
    """Every reference inside a function this rule's ``allow`` list does not exempt."""
    allow = tuple(as_str_list(options.get("allow")))
    return references().where(_in_a_function()).where(not_(allow_by_id_or_module(allow)))


def global_outer_scope_write(options: Mapping[str, Any]) -> Selection:
    """Shape 1a: a non-attribute write whose target lives at module scope.

    ``analysis.writes.outer_scope_writes`` followed by the frozen rule's
    module-scope test, clause for clause: WRITE, not an attribute access, not an
    unresolved-attribute sentinel, and a target whose parent scope is a module.
    Covers ``global x; x += 1`` after the binder's redirect and subscript writes
    on module-level containers (``CACHE[k] = v``).

    ``written_variable`` is ``strip_shadow`` of the reference's own id: a
    ``global``-redirected write targets a shadowed binding (``m:COUNTER$2``) and
    the message names the variable, not the binding.
    """

    @opaque("mutation-strip-shadow", reads=("symbol_id",))
    def _written(record: Any) -> str:
        return strip_shadow(record.symbol_id)

    return (
        _global_base(options)
        .where(row.kind.eq(ReferenceKind.WRITE))
        .where(row.is_attribute_access.eq(False))
        .where(not_(row.symbol_id.startswith(UNRESOLVED_PREFIX)))
        .where(_at_module_scope("binding_parent_scope_id"))
        .with_field("written_variable", _written)
    )


def global_mutator_call(options: Mapping[str, Any]) -> Selection:
    """Shape 2: a mutator call on a module-level container (``ITEMS.append(x)``).

    The frozen ``_mutator_call_violations`` in its order: kind, attribute
    access, a receiver root that is a module-scope ``VARIABLE``, then the method
    name's membership in the mutator table. The method here is
    :func:`pypeeker.models.leaf_name` and **not** the arm-specific
    :func:`_leaf_method` the argument rule uses; see that function.

    The message quotes ``receiver_root_symbol_id`` — the frozen
    ``root.symbol_id``, a full id rather than a bare name.
    """
    mutators = tuple(sorted(mutator_names(options)))
    return (
        _global_base(options)
        .where(row.kind.eq(ReferenceKind.CALL))
        .where(row.is_attribute_access.is_true())
        .where(row.receiver_root_kind.eq(SymbolKind.VARIABLE))
        .where(_at_module_scope("receiver_root_parent_scope_id"))
        .with_field("method", _leaf())
        .where(row.method.is_in(*mutators))
    )


def global_import_attribute_write(options: Mapping[str, Any]) -> Selection:
    """Shape 3: an attribute write rooted at an imported module (``config.value = x``).

    ``analysis.writes.attribute_writes`` filtered to ``ReceiverKind.IMPORT``,
    which is ``classify_receiver``'s answer for a receiver root of kind
    ``IMPORT`` and needs no name test — unlike the parameter arms, ``IMPORT``
    has no ``self``/``cls`` case. Subscript stores through an attribute chain
    (``os.environ["X"] = v``) arrive here too: the binder records them as
    attribute writes carrying receiver metadata.

    ``imported_module`` is the frozen display rule, ``root.imported_from or
    root.name`` — the dotted module beats the local alias. The frozen engine
    reaches it through a ``(line, attribute) -> name`` map, which would take the
    **last** writer's name if two import-rooted writes shared a line and an
    attribute while rooting at different imports; each row answers for itself
    here. Exotic and unobserved, and the map's ``"imported module"`` default is
    unreachable on either side because it is built from the identical predicate.
    """

    @opaque(
        "mutation-imported-module",
        reads=("receiver_root_imported_from", "receiver_root_name"),
    )
    def _imported_module(record: Any) -> str:
        return record.receiver_root_imported_from or record.receiver_root_name

    return (
        _global_base(options)
        .where(row.kind.eq(ReferenceKind.WRITE))
        .where(row.is_attribute_access.is_true())
        .where(row.receiver_root_kind.eq(SymbolKind.IMPORT))
        .with_field("attribute", _leaf())
        .with_field("imported_module", _imported_module)
    )


def global_rebind(options: Mapping[str, Any]) -> Selection:
    """Shape 1b: a module-level variable rebound inside a function (``global x; x = 1``).

    The one part of this rule that is not a references selection, because the
    binder emits no reference for the rebind at all — see
    :func:`pypeeker.dsl.sweeps.global_rebind_rows`. The row source publishes
    ``enclosing_function_id`` and ``enclosing_function_id_module`` under the
    same names the reference rows use, so the ``allow`` clause is the same
    builder; there is no enclosing-function clause because a rebind row exists
    only where the sweep already found one.
    """
    allow = tuple(as_str_list(options.get("allow")))
    return Selection(global_rebind_rows()).where(not_(allow_by_id_or_module(allow)))
