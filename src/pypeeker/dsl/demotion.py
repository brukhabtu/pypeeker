"""Demote and privatize: two selections over one shared mutation value.

Fork #2 of ``dsl-rewrite.md``, made concrete. The frozen tree carries two
implementations of one operation — ``cli.py``'s single-symbol ``demote``, which
builds a ``ChangeVisibilityIntent``, and ``refactor.privatize``'s batch sweep,
which builds a ``RenameIntent`` with a hand-computed ``"_" + name`` — and they
drifted on both axes that matter: two intent kinds, and two confidence floors
(``check_fixes.auto_fixable`` demands ``DECLARED``; ``_demote_candidates`` skips
only ``HEURISTIC``).

Here there is one operation, so there is one
:data:`~pypeeker.dsl.terminals.DEMOTE` value, and the two entry points differ
only in **which rows** they select:

* :func:`demote_selection` — the row a user typed an id for.
* :func:`privatize_selections` — the rows the three nomination rules find.

Neither carries a floor, a kind, or a guard of its own; all three live on the
shared value, so a divergence between the two is not something this module
could express. That is the fork's actual content: the property is structural,
not a convention two call sites are asked to respect.

The second thing that dissolves here is the nomination path. The frozen
``check/demotion.py`` recovers each candidate's symbol id by running three
regexes over the finding's **message text**, which makes rule wording a load
bearing contract (and is why fork #6 could not re-key baselines without
breaking it). A DSL row already knows what it is about: the id comes off the
row's anchor, and the regexes have no port.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pypeeker.dsl.anchors import Anchor
from pypeeker.dsl.expr import Const, row
from pypeeker.dsl.selection import Application, symbols
from pypeeker.dsl.terminals import DEMOTE
from pypeeker.dsl.visibility import (
    over_exposed_module_symbol,
    test_only_production_code,
    unused_public_symbol,
)

DEMOTE_ORIGIN_CLI = "cli"
"""Origin for a repair a user asked for directly, rather than a rule found.

The first segment of fork #5's derived ``<origin>:<mutation>:<anchor>``, so a
typed demote produces ``cli:demote:<symbol id>``. It is the workflow's name
rather than the mutation's, because ``demote:demote:<id>`` says nothing twice.
"""

DEMOTION_RULES: tuple[str, ...] = (
    "over-exposed-module-symbol",
    "unused-public-symbol",
    "test-only-production-code",
)
"""The three rules whose findings nominate a symbol for demotion.

In ``check/demotion.py``'s order, which is also the order
:func:`privatize_selections` returns them in — the frozen workflow processes
entries in input order and its pending-collision rule (first entry wins) is
deterministic only because of that.
"""


def demote_selection(anchor: Anchor) -> Application:
    """The one symbol ``anchor`` names, crossed with :data:`DEMOTE`.

    ``Const(anchor.id, anchor.evidence)`` rather than the bare string is what
    makes fork #12's evidence typing real instead of decorative.
    :class:`~pypeeker.dsl.Compare` meets both operands' evidence, so the
    anchor's confidence becomes a genuine contribution to
    :attr:`~pypeeker.dsl.Match.confidence` and therefore to whether the
    mutation's floor is cleared. A CLI-typed id is ``DECLARED``
    (:func:`~pypeeker.dsl.resolve_symbol_anchor`'s default), so it clears
    ``DEMOTE``'s ``INFERRED`` floor and applies where a ``HEURISTIC``
    finding-derived anchor is refused — which is the whole behavioural claim
    fork #12 makes about typed ids. Passing the raw string would leave that
    claim untested and unenforceable: every row would report the row's own
    evidence and the anchor's would be discarded.

    Args:
        anchor: a resolved symbol anchor. Resolution is
            :func:`~pypeeker.dsl.resolve_symbol_anchor`'s job and it raises
            rather than returning nothing, so an unresolved id never reaches
            here as an empty selection.

    Returns:
        An application over the symbols universe yielding at most one row.
        The mutation is bound **here**, not by the caller: fork #2's "one
        operation = one mutation object" is a structural claim, and a function
        returning a bare selection would leave the pairing to convention —
        which is the demote/privatize drift the fork exists to make unwritable.
    """
    return symbols().where(row.symbol_id.eq(Const(anchor.id, anchor.evidence))).apply(DEMOTE)


def privatize_selections(
    options: Mapping[str, Any],
) -> tuple[tuple[str, Application], ...]:
    """The three nomination rules' applications, paired with their rule ids.

    The rule id is the pair's first element because it is the **origin** of
    fork #5's derived id: a symbol nominated by ``unused-public-symbol``
    produces ``unused-public-symbol:demote:<symbol id>``, so a report can say
    which rule asked for a repair without a side table mapping fixes back to
    findings.

    Every symbol id comes off its row's anchor. The frozen workflow re-extracts
    it from the finding's message with three regexes, which is why its rule
    wording is a contract; nothing here reads a message.

    The rows carry the family's ``Weaken`` from
    :data:`~pypeeker.dsl.DYNAMIC_ACCESS_WEAKENED_RULES`, so a symbol in a module
    doing dynamic attribute access arrives ``HEURISTIC`` and falls below
    ``DEMOTE``'s floor — the frozen ``skip_heuristic`` behaviour, reached
    through the lattice rather than through a second filter.

    Args:
        options: the shared ``[tool.pypeeker.visibility]``-bearing option table
            each rule's builder reads, exactly as
            :data:`pypeeker.dsl.RULES` supplies it.

    Returns:
        ``(rule_id, application)`` pairs in :data:`DEMOTION_RULES` order, every
        one of them crossed with the **same** :data:`DEMOTE` object
        :func:`demote_selection` uses. That identity is the whole of fork #2:
        one operation is one mutation value, so the floor cannot be stated
        differently here than it is stated there.
    """
    builders = (
        over_exposed_module_symbol,
        unused_public_symbol,
        test_only_production_code,
    )
    return tuple(
        (rule_id, build(options).apply(DEMOTE))
        for rule_id, build in zip(DEMOTION_RULES, builders, strict=True)
    )
