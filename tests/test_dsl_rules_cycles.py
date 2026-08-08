"""``no-import-cycles`` on the DSL: which cycles fire, and which imports defer.

The differential oracle grades this rule against the frozen engine on
``tests/fixtures/parity/cycles``. These tests pin the half the oracle cannot.
pypeeker's own gated self-lint holds ``no-import-cycles`` at **zero** findings,
so the ``self`` target compares 0-vs-0; and the fixture corpus cannot carry the
``allow`` option at all, because the harness's minimal TOML writer refuses the
nested list that option needs (see the note in ``scripts/parity-manifest.toml``).

The deferred-import law is the reason this file exists. ``dsl-rewrite.md``'s
ledger records it as a **binding spec note**: an import is load-time iff every
enclosing scope up to the module is a module or class body, and a ``function``,
a ``lambda`` **or** a ``comprehension`` anywhere on the chain defers it. The
ledger also records that the strongest competing design proposal got all three
points wrong, so each is asserted on its own corpus below rather than folded
into one "nothing else fires" assertion that a single surviving bug could pass.
"""

import pytest

from pypeeker.dsl import Corpus, DslRule, Finding, Reach, dsl_rule
from pypeeker.models import Confidence

RULE = "no-import-cycles"

TAIL = "— import the type directly, or extract the shared contract to a sibling module both can depend on"


def _finding(path: str, line: int, *members: str) -> Finding:
    return Finding(
        rule=RULE,
        path=path,
        line=line,
        message=f"import cycle among modules: {', '.join(members)} {TAIL}",
        confidence=Confidence.DECLARED,
    )


def _corpus(indexed_project, files: dict[str, str]) -> Corpus:
    """Index ``files`` and scope a corpus to ``cyc/``.

    Paths are rooted at the package itself rather than under a ``src/`` prefix:
    the binder derives the module id from the indexed path verbatim, so
    ``cyc/a.py`` must be indexed as such for its module id to be ``cyc.a``.
    ``pypeeker index src`` strips the configured source root before binding,
    which is why the on-disk parity fixture does keep its ``src/`` layer.
    """
    _, store = indexed_project(files)
    return Corpus(store, ("cyc",))


def _rule() -> DslRule:
    rule = dsl_rule(RULE)
    assert isinstance(rule, DslRule)
    return rule


def _findings(corpus: Corpus, options=None) -> list[Finding]:
    return _rule().findings(options or {}, corpus)


# ---------------------------------------------------------------------------
# cycles that fire
# ---------------------------------------------------------------------------

MODULE_LEVEL = {
    "cyc/a.py": '''\
"""A."""
from cyc.b import B

A = "a"
''',
    "cyc/b.py": '''\
"""B."""
from cyc.a import A

B = "b"
''',
}


def test_a_module_level_cycle_is_reported_once_at_a_real_import_site(indexed_project):
    # One finding for the whole component, not one per member: exactly one
    # module of the component carries the sweep's answer, which is what turns
    # the per-module universe into one-finding-per-cycle. The anchor is the
    # first in-component edge site in sorted order — cyc.a's import of cyc.b,
    # on line 2 — and never the fallback line 1.
    assert _findings(_corpus(indexed_project, MODULE_LEVEL)) == [
        _finding("cyc/a.py", 2, "cyc.a", "cyc.b")
    ]


CLASS_BODY = {
    "cyc/e.py": '''\
"""E: a class body runs at import time, so this closes the cycle."""


class K:
    """K."""

    from cyc.f import F


E = "e"
''',
    "cyc/f.py": '''\
"""F."""
from cyc.e import E

F = "f"
''',
}


def test_a_class_body_import_still_closes_a_cycle(indexed_project):
    # A class body executes at module load, so an import nested in one is not
    # deferred — only function/lambda/comprehension scopes defer. The anchor is
    # cyc.e's class-body import on line 7.
    assert _findings(_corpus(indexed_project, CLASS_BODY)) == [
        _finding("cyc/e.py", 7, "cyc.e", "cyc.f")
    ]


def test_the_members_are_listed_in_sorted_order(indexed_project):
    # The graph is built from cyc.z importing cyc.m and back; the message lists
    # cyc.m first regardless, because the component is sorted before it is
    # worded. Row order — and therefore finding order — would otherwise depend
    # on Tarjan's traversal.
    reported = _findings(
        _corpus(
            indexed_project,
            {
                "cyc/z.py": '"""Z."""\nfrom cyc.m import M\n\nZ = "z"\n',
                "cyc/m.py": '"""M."""\nfrom cyc.z import Z\n\nM = "m"\n',
            },
        )
    )
    assert [f.message for f in reported] == [
        f"import cycle among modules: cyc.m, cyc.z {TAIL}"
    ]


# `cyc/a.py` and `cyc/a/__init__.py` are two indexed files with ONE module id.
# The cycle is charged to cyc.a, so a port that keyed the answer on the
# reporting module id and quantified over the per-file modules universe fired
# twice — the second time at `cyc/a/__init__.py:2`, a line copied from the
# other file's import site and blank in the file it points at.
COLLIDING_MODULE_ID = {
    "cyc/a.py": '"""A, the module."""\nfrom cyc.b import B\n\nA = "a"\n',
    "cyc/a/__init__.py": '"""A, the package."""\n\nA = "a"\n',
    "cyc/b.py": '"""B."""\nfrom cyc.a import A\n\nB = "b"\n',
}


def test_two_files_sharing_the_reporting_module_id_report_the_cycle_once(indexed_project):
    corpus = _corpus(indexed_project, COLLIDING_MODULE_ID)
    # Guard: without a real collision the assertion below proves nothing.
    assert _files_by_module_id(corpus)["cyc.a"] == ["cyc/a.py", "cyc/a/__init__.py"]
    # The frozen engine emits exactly one finding here, verified end to end
    # against `pypeeker check --strict` on a materialized copy of this tree.
    assert _findings(corpus) == [_finding("cyc/a.py", 2, "cyc.a", "cyc.b")]


def _files_by_module_id(corpus: Corpus) -> dict[str, list[str]]:
    """Indexed file paths grouped by the module id their MODULE symbol carries."""
    from pypeeker.models import SymbolKind

    grouped: dict[str, list[str]] = {}
    for index in corpus.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE), None
        )
        if module_id is not None:
            grouped.setdefault(module_id, []).append(index.file_path)
    return {key: sorted(value) for key, value in grouped.items()}


def test_two_independent_cycles_are_two_findings(indexed_project):
    files = dict(MODULE_LEVEL)
    files.update(
        {
            "cyc/p.py": '"""P."""\nfrom cyc.q import Q\n\nP = "p"\n',
            "cyc/q.py": '"""Q."""\nfrom cyc.p import P\n\nQ = "q"\n',
        }
    )
    assert sorted(f.path for f in _findings(_corpus(indexed_project, files))) == [
        "cyc/a.py",
        "cyc/p.py",
    ]


# ---------------------------------------------------------------------------
# the deferred-import law: three points, three tests
# ---------------------------------------------------------------------------

FUNCTION_LOCAL = {
    "cyc/c.py": '''\
"""C: the deferred import that breaks the cycle."""

C = "c"


def use():
    """Use."""
    from cyc.d import D

    return D
''',
    "cyc/d.py": '''\
"""D."""
from cyc.c import C

D = "d"
''',
}


def test_a_function_local_import_does_not_close_a_cycle(indexed_project):
    # It runs when the function is called, not at module load — the idiomatic
    # way to break a cycle, and the binder's own visitors do exactly this.
    assert _findings(_corpus(indexed_project, FUNCTION_LOCAL)) == []


LAMBDA_SCOPED = {
    "cyc/i.py": '''\
"""I: a lambda body is deferred just as a function body is."""
import importlib

I = "i"

_get_j = lambda: importlib.import_module("cyc.j")
''',
    "cyc/j.py": '''\
"""J."""
from cyc.i import I

J = "j"
''',
}


def test_a_lambda_scoped_import_does_not_close_a_cycle(indexed_project):
    # The binder recovers the dynamic `importlib.import_module` call as a real
    # IMPORT symbol whose parent scope is the lambda's, so this case is
    # genuinely exercised rather than vacuously silent.
    corpus = _corpus(indexed_project, LAMBDA_SCOPED)
    assert _findings(corpus) == []
    assert _recovered_dynamic_import_scopes(corpus) == ["lambda"]


COMPREHENSION_SCOPED = {
    "cyc/g.py": '''\
"""G: a comprehension gets its own scope in Python 3, so this defers too."""
import importlib

G = "g"

_mods = [importlib.import_module("cyc.h") for _ in range(1)]
''',
    "cyc/h.py": '''\
"""H."""
from cyc.g import G

H = "h"
''',
}


def test_a_comprehension_scoped_import_does_not_close_a_cycle(indexed_project):
    # The subtlest of the ledger's three points: the import sits at module
    # level by eye, but a comprehension has its own scope, so it does not run
    # at module load. A port whose deferred set held only function and lambda
    # would report a cycle here.
    corpus = _corpus(indexed_project, COMPREHENSION_SCOPED)
    assert _findings(corpus) == []
    assert _recovered_dynamic_import_scopes(corpus) == ["comprehension"]


# --- the "EVERY enclosing scope" quantifier, on its own ---------------------
#
# The three corpora above all have a chain of depth one: the import's immediate
# scope is the deferring one, so an implementation that only looked at that
# scope and never walked `parent_scope_id` would pass every one of them. These
# two corpora put a NON-deferring scope (a class body) immediately around the
# import and the deferring scope further out, which is the only shape that can
# tell the two implementations apart.

CLASS_IN_FUNCTION = {
    "cyc/c.py": '''\
"""C: class bodies run at import time — but not these ones."""

C = "c"


def outer():
    """Outer."""

    class Outer:
        """Outer class."""

        class Inner:
            """Inner class."""

            from cyc.d import D

    return Outer
''',
    "cyc/d.py": '"""D."""\nfrom cyc.c import C\n\nD = "d"\n',
}


def test_a_class_body_import_defers_when_a_function_encloses_the_class(indexed_project):
    # Chain: class -> class -> function -> module. The import's own scope is a
    # class body, which runs at import time in isolation; the function two
    # levels out is what defers it. Only a walk up every parent sees that.
    corpus = _corpus(indexed_project, CLASS_IN_FUNCTION)
    assert _enclosing_scope_kinds(corpus, "cyc/c.py", ":D") == [
        "class",
        "class",
        "function",
        "module",
    ]
    assert _findings(corpus) == []


CLASS_IN_CLASS = {
    "cyc/p.py": '''\
"""P: two class bodies and nothing else, so the import does run at load."""

P = "p"


class Outer:
    """Outer class."""

    class Inner:
        """Inner class."""

        from cyc.q import Q
''',
    "cyc/q.py": '"""Q."""\nfrom cyc.p import P\n\nQ = "q"\n',
}


def test_a_doubly_nested_class_body_import_still_closes_a_cycle(indexed_project):
    # The other half of the same law, and the reason the walk cannot simply
    # answer "deferred" for any chain longer than one: class -> class -> module
    # is entirely load-time, so this fires. The anchor is the class-body import
    # on line 11.
    corpus = _corpus(indexed_project, CLASS_IN_CLASS)
    assert _enclosing_scope_kinds(corpus, "cyc/p.py", ":Q") == ["class", "class", "module"]
    assert _findings(corpus) == [_finding("cyc/p.py", 12, "cyc.p", "cyc.q")]


COMPREHENSION_IN_METHOD = {
    "cyc/g.py": '''\
"""G: a comprehension inside a method inside a class."""
import importlib


class Holder:
    """Holder."""

    def load(self):
        """Load."""
        return [importlib.import_module("cyc.h") for _ in range(1)]


G = "g"
''',
    "cyc/h.py": '"""H."""\nfrom cyc.g import G\n\nH = "h"\n',
}


def test_a_comprehension_inside_a_method_defers_through_the_whole_chain(indexed_project):
    # Chain: comprehension -> function -> class -> module. Every kind the law
    # names appears on one chain, so nothing here can be answered by inspecting
    # a single scope.
    corpus = _corpus(indexed_project, COMPREHENSION_IN_METHOD)
    assert _enclosing_scope_kinds(corpus, "cyc/g.py", "<dynamic-import") == [
        "comprehension",
        "function",
        "class",
        "module",
    ]
    assert _findings(corpus) == []


def _enclosing_scope_kinds(corpus: Corpus, file_path: str, match: str) -> list[str]:
    """Scope kinds from the import's own scope out to the module, in that order.

    ``match`` is a substring of the import symbol's id, so a file with more than
    one import names the one it means. Asserted alongside each corpus above so a
    test cannot pass because the binder stopped nesting scopes the way the
    source reads.
    """
    from pypeeker.models import SymbolKind

    index = next(i for i in corpus.indexes if i.file_path == file_path)
    scope_by_id = {scope.scope_id: scope for scope in index.scopes}
    symbol = next(
        s for s in index.symbols if s.kind is SymbolKind.IMPORT and match in s.symbol_id
    )
    kinds: list[str] = []
    scope_id = symbol.parent_scope_id
    while scope_id is not None:
        scope = scope_by_id[scope_id]
        kinds.append(scope.kind.value)
        scope_id = scope.parent_scope_id
    return kinds


def test_a_parent_scope_the_index_does_not_carry_counts_as_load_time():
    # The frozen rule's own conservative branch, and the easiest one to flip
    # while every corpus above stays green: an unknown scope must NOT be read as
    # "deferred", or a binder gap would silently suppress real cycles.
    from pypeeker.dsl import sweeps

    assert sweeps._runs_at_import_time("no-such-scope", {}) is True
    assert sweeps._runs_at_import_time(None, {}) is True


def _recovered_dynamic_import_scopes(corpus: Corpus) -> list[str]:
    """The scope kind of every dynamically-recovered import in ``corpus``.

    Guards the two tests above against passing for the wrong reason: if the
    binder ever stopped recovering ``importlib.import_module`` as an IMPORT
    symbol, or stopped nesting it in the lambda/comprehension scope, "no
    findings" would prove nothing about the deferred-import law.
    """
    kinds: list[str] = []
    for index in corpus.indexes:
        scope_by_id = {scope.scope_id: scope for scope in index.scopes}
        for symbol in index.symbols:
            if "<dynamic-import" in symbol.symbol_id:
                scope = scope_by_id.get(symbol.parent_scope_id or "")
                kinds.append(scope.kind.value if scope else "?")
    return kinds


# ---------------------------------------------------------------------------
# the allow option — not gradeable by the oracle, so pinned here
# ---------------------------------------------------------------------------


def test_an_allow_listed_cycle_is_suppressed(indexed_project):
    corpus = _corpus(indexed_project, MODULE_LEVEL)
    assert _findings(corpus, {"allow": [["cyc.a", "cyc.b"]]}) == []


def test_allow_matches_on_the_member_set_not_on_the_written_order(indexed_project):
    corpus = _corpus(indexed_project, MODULE_LEVEL)
    assert _findings(corpus, {"allow": [["cyc.b", "cyc.a"]]}) == []


def test_a_partial_allow_entry_does_not_suppress_the_cycle(indexed_project):
    # The old rule matches the whole member set, so naming one member of a
    # two-module cycle suppresses nothing.
    corpus = _corpus(indexed_project, MODULE_LEVEL)
    assert _findings(corpus, {"allow": [["cyc.a"]]}) == [
        _finding("cyc/a.py", 2, "cyc.a", "cyc.b")
    ]


@pytest.mark.parametrize("allow", ["cyc.a", 7, {"cyc.a": "cyc.b"}, [["cyc.a", "cyc.b"], "x"]])
def test_a_malformed_allow_option_is_dropped_rather_than_raising(indexed_project, allow):
    # `_allowed_cycles` in the frozen rule silently ignores a non-list `allow`
    # and any non-list entry inside it. The last case keeps its one well-formed
    # entry, so it still suppresses.
    corpus = _corpus(indexed_project, MODULE_LEVEL)
    expected = [] if isinstance(allow, list) else [_finding("cyc/a.py", 2, "cyc.a", "cyc.b")]
    assert _findings(corpus, {"allow": allow}) == expected


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def test_the_rule_is_one_where_over_a_component_row_source(indexed_project):
    # One stage: the two clauses that are the frozen rule's two `continue`s.
    # Everything the message quotes is a field of the component row, so there
    # is nothing to derive. Reach is PROJECT because producing the rows needs
    # the whole corpus — a cycle is a statement about the whole graph.
    selection = _rule().build({})
    assert selection.stages == ("where",)
    assert selection.reach is Reach.PROJECT


def test_equivalently_ordered_allow_options_share_one_sweep(indexed_project, monkeypatch):
    # The params normalization sorts both the cycles and their members, so two
    # spellings of the same suppression are one memo entry rather than two
    # passes over the whole import graph.
    from pypeeker.dsl import sweeps

    calls: list[object] = []
    real = sweeps._cycle_sweep

    def counting(corpus, params):
        calls.append(params)
        return real(corpus, params)

    monkeypatch.setattr(sweeps, "_cycle_sweep", counting)
    corpus = _corpus(indexed_project, MODULE_LEVEL)
    _findings(corpus, {"allow": [["cyc.b", "cyc.a"]]})
    _findings(corpus, {"allow": [["cyc.a", "cyc.b"]]})
    assert len(calls) == 1
