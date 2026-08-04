"""Naming an expression, which is to say registering it as a trait provider.

This module is the join between the DSL and the analysis layer, and its name
carries the claim: **naming an expression *is* registering it into the trait
registry that already exists.** :func:`trait` writes into
:mod:`pypeeker.analysis.traits`'s ``_REGISTRY`` through the public
:func:`~pypeeker.analysis.register_trait` decorator. There is no second table,
no shadow registry, and no per-package cache of composed traits that a
consumer would then have to know about — a composed trait and a hand-written
one are looked up the same way, by
:func:`~pypeeker.analysis.get_trait_provider`, and a rule or a refactor
precondition cannot tell which kind it got.

That symmetry is the point, and it is also why the guards below are strict.
The registry is last-registration-wins with no builtin protection, so this
module refuses three things rather than registering something the registry
would happily accept:

* **A ``PROJECT``-reach expression.** A trait provider's signature is
  ``(FileIndex, symbol_id) -> Trait``: one already-loaded file, one anchor,
  no store and no resolver. An expression whose reach escaped the file has
  nothing to run against. This is architecture.md's trait-promotion rule
  clause (b) — "derivable from one already-loaded ``FileIndex`` plus one
  ``symbol_id``" — turned from a review convention into a runtime check.
* **An expression reading fields outside the symbols universe.** The same
  clause constrains the *anchor shape*, not just the substrate: a provider is
  handed a symbol id, so ``row.receiver_root_symbol_id`` (a references-universe
  field) has no value to read here and would silently evaluate to ``None``
  forever. Fork #11 keeps primitive traits hand-written precisely so the
  composed tier stays honest about what it can express.
* **Displacing a provider it did not create.** See
  :class:`~pypeeker.dsl.TraitShadowError`.

Primitive traits stay hand-written Python declaring their own confidence
(fork #11). ``type-annotation`` and ``variable-mutation`` are not reimplemented
here and are not shadowed by anything installed here; composed traits are an
*addition* to that tier, which is what the guards protect.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pypeeker.analysis import Trait, get_trait_provider, register_trait
from pypeeker.dsl.errors import ReachError, TraitShadowError, UnknownFieldError
from pypeeker.dsl.expr import EvalContext, Expr
from pypeeker.dsl.reach import Reach
# Both of these are package internals reached across modules rather than through
# the barrel: they are the pointwise halves of machinery `Selection` uses when
# quantifying, and publishing them would add names to the public surface that no
# consumer outside this package has a reason to touch.
from pypeeker.dsl.selection import _LazyTraits
from pypeeker.dsl.universes import _symbol_fields, universe_fields
from pypeeker.models import FileIndex

# Providers this module built, by the name they were registered under. Identity,
# not just presence, is what distinguishes "re-installing our own composed
# trait" (always fine, and what makes install_expressions idempotent) from
# "quietly replacing somebody else's provider" (refused).
_COMPOSED: dict[str, Callable[[FileIndex, str], Trait]] = {}

_NO_FIELDS: Mapping[str, Any] = {}


def _summary(name: str, expr: Expr) -> str:
    """Describe ``expr`` in one clause, for the middle part of a provenance string.

    Args:
        name: the trait name the expression was registered under.
        expr: the expression itself.

    Returns:
        Prose naming what the expression reads — its model fields and its
        trait dependencies — which is the "facts read" part of the provenance
        convention in :class:`pypeeker.analysis.Trait`.
    """
    fields = ", ".join(sorted(expr.field_names)) or "no fields"
    traits = ", ".join(sorted(expr.trait_names)) or "no traits"
    return f"named expression {name!r} over fields ({fields}) and traits ({traits})"


def _provider_for(name: str, expr: Expr) -> Callable[[FileIndex, str], Trait]:
    """Build the ``(FileIndex, symbol_id) -> Trait`` closure evaluating ``expr``.

    Args:
        name: the trait name, used in the provenance string.
        expr: the expression to evaluate per anchor.

    Returns:
        A provider matching the registry's signature exactly.
    """
    summary = _summary(name, expr)
    trait_names = expr.trait_names
    # The provenance convention opens with the provider's dotted module path.
    # Read off a function defined here rather than off the module global
    # ``__name__``, which the binder does not yet resolve (see the same
    # workaround and the same reason in pypeeker/check/builtin/__init__.py) —
    # and derived rather than written as a literal, so moving this module
    # cannot silently leave a stale path in every trait it produces.
    origin = _provider_for.__module__

    def _composed(file_index: FileIndex, symbol_id: str) -> Trait:
        """Evaluate the named expression for one anchor in one file."""
        # A symbol the file does not declare yields an empty row rather than an
        # exception: every field read then goes None at UNKNOWN and the
        # predicate goes false, which is the contract the builtin providers
        # already hold to (tests/test_traits.py pins it for both of them).
        fields = _symbol_fields(file_index, symbol_id)
        context = EvalContext(
            fields=_NO_FIELDS if fields is None else fields,
            traits=_LazyTraits(trait_names, file_index, symbol_id),
        )
        node = expr.evaluate(context)
        return Trait(
            value=node.value,
            confidence=node.confidence,
            provenance=f"{origin}: {summary} for '{symbol_id}' in {file_index.file_path}",
        )

    return _composed


def trait(
    name: str,
    expr: Expr,
    *,
    replace: bool = False,
) -> Callable[[FileIndex, str], Trait]:
    """Register ``expr`` as the trait provider for ``name``.

    Args:
        name: the registry key, e.g. ``"tuple-candidate"``. Consumers read it
            back through :func:`pypeeker.analysis.get_trait_provider`.
        expr: the expression to evaluate per anchor. It is quantified over the
            symbols universe, because that is the anchor shape a trait provider
            receives.
        replace: allow displacing a provider this function did not create.
            Off by default; see :class:`~pypeeker.dsl.TraitShadowError`.

    Returns:
        The registered provider, so a caller can invoke it directly without a
        registry round trip.

    Raises:
        ReachError: ``expr`` reaches ``PROJECT``, so no ``(FileIndex,
            symbol_id)`` substrate can satisfy it.
        UnknownFieldError: ``expr`` reads a field the symbols universe does not
            declare; the message names the fields it does.
        TraitShadowError: ``name`` is taken by a provider this function did not
            create and ``replace`` is false.
    """
    if expr.reach is Reach.PROJECT:
        raise ReachError(
            f"cannot name expression {name!r} as a trait: its reach is project, but a "
            f"trait provider is called as (FileIndex, symbol_id) -> Trait and has no "
            f"store and no cross-module resolver to offer it. Drop the follow step or "
            f"the 'project:' declared read, or write this one as a hand-written "
            f"primitive trait declaring its own confidence.",
            trait=name,
        )
    declared = universe_fields("symbols")
    for field_name in sorted(expr.field_names):
        if field_name not in declared:
            raise UnknownFieldError(field_name, declared, universe="symbols")
    existing = get_trait_provider(name)
    if existing is not None and existing is not _COMPOSED.get(name) and not replace:
        raise TraitShadowError(name)
    provider = _provider_for(name, expr)
    register_trait(name)(provider)
    _COMPOSED[name] = provider
    return provider
