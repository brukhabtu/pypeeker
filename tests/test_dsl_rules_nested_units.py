"""``import-boundaries`` over nested units: policing a layer below the first one.

The frozen engine's unit is one package segment beneath ``root``
(``check.rules._package_under``), which is all a flat layout ever needs. A
project on a ``src/<pkg>/`` layout has its real boundaries a layer deeper —
``domain.orders`` against ``domain.billing`` — and against the flat engine a
dotted key matches no unit and is silently inert (TASK-169).

The DSL engine resolves a module to the **longest declared unit prefix**
instead. These tests pin both halves of that: that nested units are enforced in
both directions, and that a configuration which names no nested unit cannot
observe the change — which is what keeps the differential oracle at parity with
the frozen engine without a divergence entry.
"""

import pytest

from pypeeker.dsl import Corpus, Finding, MultiPartRule, dsl_rule
from pypeeker.dsl import sweeps
from pypeeker.models import Confidence

RULE = "import-boundaries"

# A project whose architecture lives one layer below the first segment:
#
#   api           -> may import domain.orders, and does
#   domain.orders -> may import nothing, and imports domain.billing anyway
#   domain.billing-> a leaf
#   domain        -> the bare namespace layer the two nested units sit under
#
# Against the flat engine every one of these files collapses into `api` or
# `domain`, and the orders -> billing edge is invisible by construction.
NESTED_FILES = {
    "app/__init__.py": "",
    "app/api/__init__.py": "",
    "app/api/handler.py": '''\
"""Api handler: reaches the domain layer through its permitted unit."""
from app.domain.orders.order import make


def handle():
    """Handle."""
    return make()
''',
    "app/domain/__init__.py": '"""The domain namespace layer itself."""\n',
    "app/domain/orders/__init__.py": "",
    "app/domain/orders/order.py": '''\
"""Orders, reaching sideways into billing."""
from app.domain.billing.charge import bill


def make():
    """Make an order."""
    return bill()
''',
    "app/domain/billing/__init__.py": "",
    "app/domain/billing/charge.py": '''\
"""Billing leaf."""


def bill():
    """Bill."""
    return 1
''',
}

NESTED_OPTIONS = {
    "root": "app",
    "strict": True,
    "unconstrained": ["domain"],
    "allow": {"api": ["domain.orders"], "domain.orders": [], "domain.billing": []},
}


@pytest.fixture
def nested_corpus(indexed_project):
    """The nested project above, indexed, scoped to ``app/``."""
    _, store = indexed_project(NESTED_FILES)
    return Corpus(store, ("app",))


def _findings(corpus, options=NESTED_OPTIONS):
    rule = dsl_rule(RULE)
    assert isinstance(rule, MultiPartRule)
    return rule.findings(options, corpus)


# ---------------------------------------------------------------------------
# the capability
# ---------------------------------------------------------------------------


def test_a_nested_unit_is_policed_against_its_sibling(nested_corpus):
    """The orders -> billing edge, which the flat engine cannot see at all."""
    assert (
        Finding(
            rule=RULE,
            path="app/domain/orders/order.py",
            line=2,
            message=(
                "package 'domain.orders' may not import 'domain.billing' "
                "(via 'app.domain.billing.charge.bill')"
            ),
            confidence=Confidence.DECLARED,
        )
        in _findings(nested_corpus)
    )


def test_a_permitted_edge_into_a_nested_unit_does_not_fire(nested_corpus):
    """``api -> domain.orders`` is declared, so the dependency side resolves too.

    The complement of the test above: a nested name has to be matchable as the
    *imported* unit as well as the importing one, or declaring it would turn
    every inbound edge into a violation instead of enforcing anything.
    """
    assert not [
        f for f in _findings(nested_corpus) if f.path == "app/api/handler.py"
    ]


def test_a_declared_nested_unit_is_not_reported_undeclared(nested_corpus):
    """The strict census counts the nested units, not the layer they collapse into."""
    census = [f for f in _findings(nested_corpus) if "is not declared" in f.message]
    assert census == []


def test_the_bare_namespace_layer_still_answers_for_itself(indexed_project):
    """``app/domain/__init__.py`` belongs to no nested unit and must be declared.

    Nested units do not absorb the layer above them: a module sitting directly
    in ``app.domain`` is covered by neither ``domain.orders`` nor
    ``domain.billing``, so under ``strict`` it is exactly the code that would
    otherwise slip enforcement silently. Dropping ``domain`` from
    ``unconstrained`` must therefore flag it.
    """
    _, store = indexed_project(NESTED_FILES)
    corpus = Corpus(store, ("app",))
    options = {**NESTED_OPTIONS, "unconstrained": []}
    census = [f for f in _findings(corpus, options) if "is not declared" in f.message]
    assert [(f.path, f.message) for f in census] == [
        (
            "app/domain/__init__.py",
            "package 'domain' is not declared in import-boundaries "
            "(add it to [tool.pypeeker.import-boundaries.allow] or to "
            "the 'unconstrained' list)",
        )
    ]


# ---------------------------------------------------------------------------
# parity: a flat configuration cannot observe any of the above
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "app.api",
        "app.api.handler",
        "app.domain.orders.order",
        "app.api.deep.deeper.deepest",
        "app",
        "other.api.handler",
    ],
)
def test_flat_vocabulary_reduces_to_the_frozen_answer(module_path):
    """With no dotted name declared, the nested resolver *is* the flat one.

    This is the whole parity argument in one assertion: the differential oracle
    grades five targets, none of which declares a nested unit, so both engines
    see identical units and emit identical findings. No divergence entry is
    needed because there is no divergence to declare.
    """
    flat = frozenset({"api", "core", "domain", "db"})
    assert sweeps._unit_under(module_path, "app", flat) == sweeps._package_under(
        module_path, "app"
    )


def test_an_undeclared_path_falls_back_to_its_first_segment():
    """A module no declared prefix covers keeps the flat engine's answer."""
    vocab = frozenset({"domain.orders"})
    assert sweeps._unit_under("app.other.thing", "app", vocab) == "other"


def test_the_longest_declared_prefix_wins():
    """Declaring both a unit and a unit inside it resolves to the more specific."""
    vocab = frozenset({"domain", "domain.orders"})
    assert sweeps._unit_under("app.domain.orders.order", "app", vocab) == "domain.orders"
    assert sweeps._unit_under("app.domain.other", "app", vocab) == "domain"


def test_the_reported_ui_widgets_case(indexed_project):
    """The shape a consumer project reported: ``ui.widgets`` must not import ``ui.app``.

    Reported against pypeeker 0.1.0 from a textual-tui project, where
    ``ui/widgets/bar.py`` collapsed into ``ui`` and the rule could not express
    the boundary at all — the constraint was worked around by moving it out to
    ast-grep. Pinned here as the real-world case, not a synthetic one.
    """
    _, store = indexed_project(
        {
            "harness_tui/__init__.py": '"""Harness."""\n',
            "harness_tui/ui/__init__.py": '"""Ui layer."""\n',
            "harness_tui/ui/app/__init__.py": "",
            "harness_tui/ui/app/shell.py": (
                '"""Shell."""\n\n\ndef boot():\n    """Boot."""\n    return 1\n'
            ),
            "harness_tui/ui/widgets/__init__.py": "",
            "harness_tui/ui/widgets/bar.py": '''\
"""Bar widget reaching back up into the app layer."""
from harness_tui.ui.app.shell import boot


def draw():
    """Draw."""
    return boot()
''',
        }
    )
    options = {
        "root": "harness_tui",
        "strict": True,
        "unconstrained": ["ui"],
        "allow": {"ui.widgets": [], "ui.app": []},
    }
    messages = [
        f.message for f in _findings(Corpus(store, ("harness_tui",)), options)
    ]
    assert (
        "package 'ui.widgets' may not import 'ui.app' "
        "(via 'harness_tui.ui.app.shell.boot')" in messages
    )


def test_the_vocabulary_collects_every_position_a_unit_name_appears_in():
    """Importer keys, dependency values and ``unconstrained`` all name units."""
    allow = {"api": {"domain.orders"}, "domain.orders": set()}
    assert sweeps._unit_vocabulary(allow, ("infra.cache",)) == frozenset(
        {"api", "domain.orders", "infra.cache"}
    )
