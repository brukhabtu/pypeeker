"""Pin ``born_private_surface`` to the seed the frozen born-private rule wrote.

``born-private`` is a ratchet, and a ratchet is only as good as the surface it
was armed against. A rule-level comparison cannot grade the **seed**, because
the rule writes the baseline and returns ``[]``: on an unseeded project it
reports nothing whatever the seed contained. The only moment the two engines'
seeds could be compared was while both existed, so the seed the frozen engine
wrote for each option shape was captured then and is recorded below as a
literal.

Those literals are the contract now. :func:`pypeeker.dsl.born_private_surface`
must reproduce them exactly, for every option the candidate prefix reads.
"""

import pytest

from pypeeker.dsl import Corpus, born_private_surface, install_expressions

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
    pytest.param(
        {},
        {
            "pkg.core:Deco",
            "pkg.core:helper",
            "pkg.core:used_elsewhere",
            "pkg.other:go",
        },
        id="defaults",
    ),
    pytest.param(
        {"kinds": ["function", "class", "variable"]},
        {
            "pkg.core:CONSTANT",
            "pkg.core:Deco",
            "pkg.core:helper",
            "pkg.core:used_elsewhere",
            "pkg.other:go",
        },
        id="explicit-kinds",
    ),
    pytest.param(
        {"allow": ["*helper*"]},
        {"pkg.core:Deco", "pkg.core:used_elsewhere", "pkg.other:go"},
        id="allow-glob",
    ),
    pytest.param(
        {"visibility": {"mode": "library"}},
        {
            "pkg.core:Deco",
            "pkg.core:helper",
            "pkg.core:used_elsewhere",
            "pkg.other:go",
        },
        id="library-mode",
    ),
    pytest.param(
        {"visibility": {"mode": "library", "public-roots": ["pkg"]}},
        {
            "pkg.core:Deco",
            "pkg.core:helper",
            "pkg.core:used_elsewhere",
            "pkg.other:go",
        },
        id="library-public-roots",
    ),
    pytest.param(
        {"allow-decorators": ["cache"], "visibility": {"allow-decorators": ["deco"]}},
        {"pkg.core:helper", "pkg.core:used_elsewhere", "pkg.other:go"},
        id="merged-allow-decorators",
    ),
]
"""Every option the candidate prefix reads, paired with the seed the frozen rule
wrote for it — including the rule-level and project-level ``allow-decorators``
merge, which is the one option read from two places at once."""


@pytest.mark.parametrize("options, expected", _OPTION_SHAPES)
def test_the_dsl_surface_equals_the_recorded_seed(indexed_project, options, expected):
    """What the DSL seeds is what the frozen rule seeded, symbol for symbol."""
    install_expressions()
    _root, store = indexed_project(_FILES)

    surface = born_private_surface(options).rows(Corpus(store, ()))
    assert {match.fields["symbol_id"] for match in surface} == expected
