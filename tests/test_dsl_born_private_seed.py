"""TEMPORARY: grade ``born_private_surface`` against the frozen born-private seed.

``born-private`` is a ratchet, and a ratchet is only as good as the surface it
was armed against. The differential oracle grades the *rule* — findings per
run — but it cannot grade the **seed**, because the frozen rule writes the
baseline and returns ``[]``: on an unseeded project both engines report nothing
whatever the seed contained. The only moment the two seeds can be compared is
while both engines exist, which is now.

So this file imports the frozen ``check.builtin.born_private._born_private`` on
purpose, runs it for its write effect, and asserts the file it wrote equals
:func:`pypeeker.dsl.born_private_surface` over the same corpus and the same
options. It is scoped to exactly that one comparison and lives in a file of its
own so that phase B can delete it whole, with a ledger line, rather than
performing surgery on a file that also holds tests worth keeping.
"""

import pytest

from pypeeker.check.builtin.born_private import _born_private
from pypeeker.check.context import CheckContext
from pypeeker.dsl import Corpus, born_private_surface, install_expressions
from pypeeker.storage import baseline_path, load_symbol_baseline

_FILES = {
    "pkg/__init__.py": '"""Barrel."""\n\nfrom pkg.core import Exported\n\n__all__ = ["Exported"]\n',
    "pkg/core.py": (
        '"""Core."""\n\n\n'
        'class Exported:\n    """E."""\n\n\n'
        'def helper() -> int:\n    """H."""\n    return 1\n\n\n'
        'def used_elsewhere() -> int:\n    """U."""\n    return 2\n\n\n'
        'def _private() -> int:\n    """P."""\n    return 3\n\n\n'
        "CONSTANT = 4\n\n\n"
        'def main() -> None:\n    """M."""\n\n\n'
        '@deco\nclass Deco:\n    """D."""\n'
    ),
    "pkg/other.py": (
        '"""Other."""\n\nfrom pkg.core import used_elsewhere\n\n\n'
        'def go() -> int:\n    """G."""\n    return used_elsewhere()\n'
    ),
    "pkg/__main__.py": '"""Main."""\n\n\ndef entry() -> None:\n    """E."""\n',
}
"""A barrel, a ``__main__``, a ``main``, a private, a constant, a decorated class,
and a symbol used from a sibling module — one instance of every exemption the
candidate prefix makes, so an omitted clause changes the compared set."""

_OPTION_SHAPES = [
    pytest.param({}, id="defaults"),
    pytest.param({"kinds": ["function", "class", "variable"]}, id="explicit-kinds"),
    pytest.param({"allow": ["*helper*"]}, id="allow-glob"),
    pytest.param({"visibility": {"mode": "library"}}, id="library-mode"),
    pytest.param(
        {"visibility": {"mode": "library", "public-roots": ["pkg"]}},
        id="library-public-roots",
    ),
    pytest.param(
        {"allow-decorators": ["cache"], "visibility": {"allow-decorators": ["deco"]}},
        id="merged-allow-decorators",
    ),
]
"""Every option the candidate prefix reads, including the rule-level and
project-level ``allow-decorators`` merge, which is the one option read from two
places at once."""


@pytest.mark.parametrize("options", _OPTION_SHAPES)
def test_the_dsl_surface_equals_the_frozen_seed(indexed_project, options):
    """What the DSL would seed is byte-for-byte what the frozen rule does seed."""
    install_expressions()
    root, store = indexed_project(_FILES)
    indexes = [
        index
        for index in (store.load(path) for path in store.list_indexed_files())
        if index is not None
    ]

    assert _born_private(CheckContext(store, indexes), dict(options)) == [], (
        "the frozen rule must be seeding here, not reporting — otherwise this "
        "test is comparing against an already-armed ratchet"
    )
    frozen_seed = load_symbol_baseline(baseline_path(root))
    assert frozen_seed, "an empty seed would make the comparison below vacuous"

    surface = born_private_surface(options).rows(Corpus(store, ()))
    assert {match.fields["symbol_id"] for match in surface} == frozen_seed
