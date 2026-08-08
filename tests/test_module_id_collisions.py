"""Pins the module-id-collision contract: storage-transaction-architecture.md ->
"Symbol IDs" -> "Module ids are not injective over files" (cross-referenced from
architecture.md's Layer 2 section).

``paths.module_path_from`` is many-to-one over indexed files, so two files can
bind the same dotted module id (and, below it, the same symbol id for any local
name they both declare). Ids are not injective *within* a file either: the
``$N`` suffix disambiguates shadowed scope-owning names only, so a re-entered
``<comprehension>`` scope, a redeclaration, or ``@typing.overload`` each bind
one id more than once.

Each test below pins one audited consumer's documented behaviour under a
collision: either a fix (query/engine.py, treebuild.py, dsl/selection.py) or a
deliberately-left, now-documented election — ``Corpus.locate`` (first-wins),
``CrossModuleResolver`` (last-wins), ``analysis.context._resolve_function``
(kind-preferring, and so able to elect the last file), and the per-file
``{symbol_id: Symbol}`` maps, which split between ``setdefault`` first-wins
(``analysis.type_annotation``) and last-wins (``analysis.calls``,
``analysis.writes``).

The colliding fixture shape (``app/dup/mod.py`` vs ``app/dup/mod/__init__.py``,
both binding module id ``app.dup.mod``) mirrors
``tests/fixtures/parity/boundaries/src/app/dup`` without touching that graded
corpus; it's built here through ``indexed_project``, which binds with
``module_path_from(name)`` and no configured source roots.
"""

from __future__ import annotations

from collections import Counter

from pypeeker.analysis import ReceiverKind
from pypeeker.analysis.context import _resolve_function
from pypeeker.analysis.type_annotation import _symbols_by_id as type_annotation_symbols_by_id
from pypeeker.analysis.writes import attribute_writes
from pypeeker.dsl import Corpus, modules, symbols
from pypeeker.models import SymbolKind
from pypeeker.paths import module_path_from
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.treebuild import _reconcile_tree as reconcile_tree

# ---------------------------------------------------------------------------
# the generator: paths.module_path_from is many-to-one over indexed files
# ---------------------------------------------------------------------------


def test_module_path_from_is_not_injective():
    # (a) a module and a same-named package both claim one dotted id.
    assert module_path_from("app/dup/mod.py") == "app.dup.mod"
    assert module_path_from("app/dup/mod/__init__.py") == "app.dup.mod"

    # (b) the same relative path repeated under two configured source roots.
    assert module_path_from("src/dup/mod.py", ("src", "lib")) == "dup.mod"
    assert module_path_from("lib/dup/mod.py", ("src", "lib")) == "dup.mod"

    # (c) one configured source root nested inside another; the first
    # matching root wins, colliding a nested file with a top-level one.
    assert module_path_from("src/app/x.py", ("src/app", "src")) == "x"
    assert module_path_from("src/x.py", ("src/app", "src")) == "x"

    # (d) a source root that is itself a package: its __init__.py maps to
    # the empty module id, and a second such root collides on "" too.
    assert module_path_from("pkgA/__init__.py", ("pkgA", "pkgB")) == ""
    assert module_path_from("pkgB/__init__.py", ("pkgA", "pkgB")) == ""


# ---------------------------------------------------------------------------
# query.SemanticQueryEngine: fixed (row-shaped _module_to_indexes)
# ---------------------------------------------------------------------------


def test_document_symbols_covers_every_file_sharing_a_module_id(indexed_project):
    _, store = indexed_project({
        "app/dup/mod.py": "def go():\n    return 1\n",
        "app/dup/mod/__init__.py": "def other():\n    return 2\n",
    })
    engine = SemanticQueryEngine(store)

    names = {s["name"] for s in engine.document_symbols("app.dup.mod")}

    assert names == {"go", "other"}


def test_members_below_the_module_boundary_covers_every_colliding_file(indexed_project):
    _, store = indexed_project({
        "app/dup/mod.py": "class C:\n    def m(self):\n        return 1\n",
        "app/dup/mod/__init__.py": "x = 1\n",
    })
    engine = SemanticQueryEngine(store)

    members = engine.members("app.dup.mod:C")

    assert [m["name"] for m in members] == ["m"]


def test_members_reports_both_symbols_that_share_one_id(indexed_project):
    _, store = indexed_project({
        "app/dup/mod.py": "def go():\n    return 1\n",
        "app/dup/mod/__init__.py": "def go():\n    return 2\n",
    })
    engine = SemanticQueryEngine(store)

    members = engine.members("app.dup.mod")

    go_entries = [m for m in members if m["name"] == "go"]
    assert len(go_entries) == 2
    assert {e["location"]["file_path"] for e in go_entries} == {
        "app/dup/mod.py",
        "app/dup/mod/__init__.py",
    }


# ---------------------------------------------------------------------------
# treebuild: fixed (_elected_manifest restores the no-change fast path)
# ---------------------------------------------------------------------------


def test_reconcile_tree_fast_path_hits_under_a_module_id_collision(bind_source):
    indexes = [
        bind_source("def go():\n    return 1\n", file_path="app/dup/mod.py"),
        bind_source("def other():\n    return 2\n", file_path="app/dup/mod/__init__.py"),
    ]

    first = reconcile_tree(indexes, None)
    again = reconcile_tree(indexes, first.tree)

    assert again.tree is first.tree
    assert again.rebuilt == set()
    assert again.removed == set()


# ---------------------------------------------------------------------------
# dsl/selection._apply_follow: fixed (dedupe keys on anchor id + source file)
# ---------------------------------------------------------------------------


def test_follow_module_keeps_one_row_per_file_under_a_collision(indexed_project):
    _, store = indexed_project({
        "app/dup/mod.py": "def go():\n    return 1\n",
        "app/dup/mod/__init__.py": "def other():\n    return 2\n",
    })
    corpus = Corpus(store)

    followed = symbols().follow("module").rows(corpus)
    every_module_row = modules().rows(corpus)

    assert len(followed) == len(every_module_row) == 2
    assert {m.fields["file_path"] for m in followed} == {
        "app/dup/mod.py",
        "app/dup/mod/__init__.py",
    }


def test_follow_module_still_collapses_one_file_to_one_row(indexed_project):
    _, store = indexed_project({
        "app.py": (
            "def go():\n    return 1\n\n\nclass Widget:\n    def paint(self):\n        return None\n"
        ),
    })
    corpus = Corpus(store)

    followed = symbols().follow("module").rows(corpus)

    assert len(followed) == 1
    assert followed[0].fields["file_path"] == "app.py"


# ---------------------------------------------------------------------------
# the two elections left in place: documented, not unified
# ---------------------------------------------------------------------------


def _colliding_alias_project(indexed_project):
    """A function and, in the colliding file, an import alias sharing one id.

    ``app/dup/mod.py`` defines ``go`` as a plain function; ``app/dup/mod/
    __init__.py`` binds the same id ``app.dup.mod:go`` to an import alias of
    ``app.dup.other:thing``. The two symbols behind one id let
    ``Corpus.locate`` and ``CrossModuleResolver`` disagree observably: which
    file's symbol is returned, and what ``resolve_definition`` resolves to.
    """
    return indexed_project({
        "app/dup/mod.py": "def go():\n    return 1\n",
        "app/dup/other.py": "def thing():\n    return 2\n",
        "app/dup/mod/__init__.py": "from app.dup.other import thing as go\n",
    })


def test_locate_elects_the_first_indexed_file(indexed_project):
    _, store = _colliding_alias_project(indexed_project)
    corpus = Corpus(store)

    located = corpus.locate("app.dup.mod:go")

    assert located is not None
    symbol, file_index = located
    assert file_index.file_path == "app/dup/mod.py"
    assert symbol.kind == SymbolKind.FUNCTION


def test_resolver_elects_the_last_indexed_file(indexed_project):
    _, store = _colliding_alias_project(indexed_project)
    corpus = Corpus(store)

    resolved = corpus.resolver.resolve_definition("app.dup.mod:go")

    assert resolved == "app.dup.other:thing"


def test_resolve_function_prefers_a_callable_over_indexed_path_order(indexed_project):
    """``_resolve_function`` is kind-preferring, so it can elect the LAST file.

    Pins the third election convention documented alongside first-wins and
    last-wins: ``analysis.context._resolve_function`` returns the first
    FUNCTION/METHOD in ``find_symbol`` order, falling back to ``matches[0]``.
    Here the first indexed file binds a VARIABLE and the second a FUNCTION, so
    it elects the second — disagreeing observably with ``Corpus.locate`` on the
    very same project. ``AnalysisContext.for_function`` uses it, so
    ``purity()``/``impurities()`` inherit the disagreement.
    """
    _, store = indexed_project({
        "app/dup/mod.py": "go = 1\n",
        "app/dup/mod/__init__.py": "def go():\n    return 2\n",
    })
    engine = SemanticQueryEngine(store)

    order = [
        (s.location.file_path, s.kind) for s in engine.find_symbol("app.dup.mod:go")
    ]
    assert order == [
        ("app/dup/mod.py", SymbolKind.VARIABLE),
        ("app/dup/mod/__init__.py", SymbolKind.FUNCTION),
    ]

    elected = _resolve_function(engine, "app.dup.mod:go")
    assert elected is not None
    assert elected.location.file_path == "app/dup/mod/__init__.py"
    assert elected.kind == SymbolKind.FUNCTION

    located = Corpus(store).locate("app.dup.mod:go")
    assert located is not None
    assert located[1].file_path == "app/dup/mod.py"
    assert located[0].kind == SymbolKind.VARIABLE


# ---------------------------------------------------------------------------
# intra-file duplicates: the $N suffix does not make ids injective per file
# ---------------------------------------------------------------------------


def _duplicate_ids(file_index) -> dict[str, int]:
    counts = Counter(s.symbol_id for s in file_index.symbols)
    return {sid: n for sid, n in counts.items() if n > 1}


def test_a_re_entered_comprehension_scope_binds_one_id_twice(bind_source):
    index = bind_source(
        "def f(xs, ys):\n"
        "    a = [i for i in xs]\n"
        "    b = [i for i in ys]\n"
        "    return a, b\n",
        file_path="mod.py",
    )

    assert _duplicate_ids(index) == {"mod:f.<comprehension>:i": 2}


def test_a_redeclared_method_binds_its_parameter_id_twice(bind_source):
    index = bind_source(
        "class C:\n"
        "    def m(self):\n"
        "        return 1\n"
        "    def m(self):\n"
        "        return 2\n",
        file_path="mod.py",
    )

    assert _duplicate_ids(index) == {"mod:C.m:self": 2}


def test_typing_overload_binds_one_parameter_id_per_signature(bind_source):
    index = bind_source(
        "import typing\n"
        "\n"
        "\n"
        "@typing.overload\n"
        "def f(a: int) -> int: ...\n"
        "@typing.overload\n"
        "def f(a: str) -> str: ...\n"
        "def f(a):\n"
        "    return a\n",
        file_path="mod.py",
    )

    assert _duplicate_ids(index) == {"mod:f:a": 3}


def test_intra_file_duplicates_can_disagree_on_kind(bind_source):
    """The two intra-file election conventions pick different symbols here.

    ``mod:C.m:x`` is bound twice, once VARIABLE and once IMPORT, so
    ``analysis.type_annotation._symbols_by_id`` (``setdefault``, first-wins)
    and the plain ``{s.symbol_id: s}`` comprehension used by
    ``analysis.calls``/``analysis.writes`` (last-wins) disagree on the same
    id in the same file.
    """
    index = bind_source(
        "class C:\n"
        "    def m(self):\n"
        "        x = 1\n"
        "        return x\n"
        "    def m(self):\n"
        "        import x\n"
        "        return x\n",
        file_path="mod.py",
    )

    assert _duplicate_ids(index)["mod:C.m:x"] == 2
    assert type_annotation_symbols_by_id(index)["mod:C.m:x"].kind == SymbolKind.VARIABLE
    last_wins = {s.symbol_id: s for s in index.symbols}
    assert last_wins["mod:C.m:x"].kind == SymbolKind.IMPORT


def test_attribute_write_receiver_follows_the_last_declaration(analysis_context):
    """``analysis.writes`` elects last-declaration-wins, and it is purity-visible.

    Same function, same reference, two declaration orders: the receiver kind
    tracks whichever of the colliding declarations comes last in CST order.
    ``IMPORT`` is an externally-visible mutation and ``VARIABLE`` is
    pure-local, which is why switching this map to ``setdefault`` would move
    frozen-engine output and is deferred to a ledger entry.
    """
    var_then_import = analysis_context(
        "def f(flag):\n"
        "    x = object()\n"
        "    import x\n"
        "    x.attr = 1\n"
        "    return flag\n",
        "mod:f",
    )
    import_then_var = analysis_context(
        "def f(flag):\n"
        "    import x\n"
        "    x = object()\n"
        "    x.attr = 1\n"
        "    return flag\n",
        "mod:f",
    )

    assert [w.receiver_kind for w in attribute_writes(var_then_import)] == [
        ReceiverKind.IMPORT
    ]
    assert [w.receiver_kind for w in attribute_writes(import_then_var)] == [
        ReceiverKind.VARIABLE
    ]
