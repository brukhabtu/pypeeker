"""The builtin composed expressions, and the explicit call that installs them.

Phase 2 claims no rules — the parity manifest is empty and stays empty until
phase 3 — so this module ships exactly one expression, and it ships it to prove
a semantic rather than to port a rule. :data:`TUPLE_CANDIDATE` is the shape
``check.rules.prefer_tuple`` selects, written in the DSL, and the divergence
ledger in ``dsl-rewrite.md`` pins what it must report::

    *(spec note)* `prefer-tuple` reports at DECLARED via the meta-read law
    (fork #4); a port that meets to INFERRED is wrong, not divergent.

It is a new registry name. It does not shadow ``type-annotation`` or
``variable-mutation``, does not register a rule, and does not emit a
``Violation`` — the frozen oracle is untouched by anything here.

**Nothing registers at import time.** The expression below is pure data
construction; installation happens only when a caller runs
:func:`install_expressions`. Two reasons, and the second outlives the first:

* ``import-time-side-effects`` is one of the eleven self-lint gated rules. The
  existing ``@register_trait`` decorators survive it because the decorator's
  own body is pure — the table write lives in a nested function the purity walk
  does not reach through the decorator protocol. A bare module-level
  ``trait(name, expr)`` call takes a different path through that analysis, and
  betting the zero-finding gate on an unquantified difference is a poor trade
  for saving one line at the call site.
* The registry is last-registration-wins with no builtin guard. A library
  module that mutates it merely because somebody imported it is rude regardless
  of what any linter thinks, and it makes the moment of registration invisible
  in a stack trace.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pypeeker.analysis import TYPE_ANNOTATION, VARIABLE_MUTATION
from pypeeker.dsl.errors import UnknownExpressionError
from pypeeker.dsl.expr import Expr, all_of, not_, row, trait_of
from pypeeker.dsl.naming import trait
from pypeeker.models import Confidence, ScopeKind, SymbolKind

TUPLE_CANDIDATE = all_of(
    row.kind.eq(SymbolKind.VARIABLE),
    row.scope_kind.is_in(ScopeKind.FUNCTION, ScopeKind.LAMBDA),
    trait_of(TYPE_ANNOTATION).at(Confidence.INFERRED).eq("list"),
    not_(trait_of(VARIABLE_MUTATION).value.attr("is_mutated")),
    not_(trait_of(VARIABLE_MUTATION).value.attr("escaping_read")),
)
"""A function-local list binding that could have been a tuple.

Five clauses, in written order: it is a variable; it lives directly in a
function or lambda body; its annotation is a ``list`` the binder *inferred*
rather than one the author declared; nothing writes through it; and no read of
it escapes into a position where a tuple would behave differently.

The third clause is the load-bearing one. ``trait_of(T).at(INFERRED)`` states
and verifies the exact confidence level it expects, so it is a meta-read and
reports ``DECLARED`` — and the whole conjunction therefore meets to
``DECLARED``, satisfying the ledger. Spelling that clause
``trait_of(T).value.eq("list")`` instead asserts nothing about the level, meets
the trait's own ``INFERRED`` in, and lands at ``INFERRED``. The two spellings
are both legal and deliberately differ; see
:class:`pypeeker.dsl.TraitAssertion`.
"""

EXPRESSIONS: Mapping[str, Expr] = MappingProxyType({
    "tuple-candidate": TUPLE_CANDIDATE,
})
"""Every builtin composed expression, by the name it installs under."""


def expression(name: str) -> Expr:
    """Look up a builtin composed expression by name.

    Args:
        name: a key of :data:`EXPRESSIONS`.

    Returns:
        The expression registered under ``name``.

    Raises:
        UnknownExpressionError: no builtin expression has that name; the
            message lists the ones that do, which is how a caller discovers
            them without a second command.
    """
    found = EXPRESSIONS.get(name)
    if found is None:
        raise UnknownExpressionError(name, EXPRESSIONS)
    return found


def install_expressions() -> tuple[str, ...]:
    """Register every builtin composed expression as a trait provider.

    Idempotent: :func:`pypeeker.dsl.trait` treats re-registering a provider it
    created itself as ordinary replacement rather than shadowing, so calling
    this on every CLI invocation is correct and cheap. It is *not* a no-op on
    the second call — a fresh closure is registered — but the observable
    behavior of the registry is unchanged.

    Returns:
        The installed names, sorted.
    """
    for name, expr in EXPRESSIONS.items():
        trait(name, expr)
    return tuple(sorted(EXPRESSIONS))
