"""Tests for batch demotion of over-exposed public symbols (TASK-92).

Covers the pre-filter inventory of :func:`demote_candidates` (one test per
surviving skip reason, including the deterministic pending-collision rule) and
an end-to-end :func:`~pypeeker.refactor.plan_privatize` run over a fixture
package: plain + barrel-exported symbols are renamed everywhere (including the
``__init__`` re-export rewrite), an override method is skipped with a hierarchy
reason, the applied tree still compiles and re-indexes without new unresolved
references, and rollback restores every byte.

At the flip (TASK-157, A13) the pre-filter lost its three *pointwise* branches
— ``heuristic-confidence``, ``dunder-or-main`` and ``already-private`` — to
:data:`pypeeker.dsl.DEMOTE`'s confidence floor and preconditions, and
``plan_privatize`` stopped nominating symbols by id: it now takes the
:class:`~pypeeker.intents.intents.ChangeVisibilityIntent` objects the mutation
already decided on. The scenarios for the retired branches live in
``tests/test_dsl_terminals.py``, and ``tests/test_refactor_privatize_intents.py``
pins that they cannot come back as skip rows here.
"""

from __future__ import annotations

from pypeeker.intents import ChangeVisibilityIntent
from pypeeker.models import is_builtin
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor import TransactionApplier, plan_privatize
from pypeeker.refactor.privatize import (
    PRIVATIZE_OPERATION,
    _demote_candidates as demote_candidates,
)
from pypeeker.resolve import CrossModuleResolver
from pypeeker.storage import IndexStore

LIBRARY_PYPROJECT = (
    '[project]\nname = "test"\n\n[tool.pypeeker.visibility]\nmode = "library"\n'
)

BARREL_FILES = {
    "pkg/__init__.py": "from pkg.mod import helper\n",
    "pkg/mod.py": "def helper():\n    return 1\n",
    "app.py": "from pkg import helper\n\nhelper()\n",
}


def _skip_reasons(skipped) -> dict[str, str]:
    """Map submitted id -> skip reason for compact assertions."""
    return {entry.symbol_id: entry.reason for entry in skipped}


def _demote(symbol_id: str, origin: str = "over-exposed-module-symbol"):
    """The demote intent the DSL's ``DEMOTE`` mutation would have produced.

    ``plan_privatize`` is handed intents, not ids: the decision to demote (and
    the confidence behind it) belongs to the mutation, so the derived
    ``<rule>:demote:<symbol>`` id is part of what arrives.
    """
    return ChangeVisibilityIntent(f"{origin}:demote:{symbol_id}", symbol_id, "demote")


# ---------------------------------------------------------------------------
# Pre-filter inventory: one test per skip reason
# ---------------------------------------------------------------------------


class TestDemoteCandidates:
    def test_not_found(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        candidates, skipped = demote_candidates(store, ["mod:nope"])
        assert candidates == []
        assert _skip_reasons(skipped) == {"mod:nope": "not-found"}

    def test_ambiguous(self, indexed_project):
        _, store = indexed_project(
            {"a.py": "def helper():\n    pass\n", "b.py": "def helper():\n    pass\n"}
        )
        candidates, skipped = demote_candidates(store, ["helper"])
        assert candidates == []
        assert _skip_reasons(skipped) == {"helper": "ambiguous"}
        assert "a:helper" in skipped[0].detail and "b:helper" in skipped[0].detail

    def test_a_plain_public_symbol_is_a_candidate(self, indexed_project):
        # The control the retired confidence scenarios used to provide: a
        # nominated symbol with nothing wrong with it becomes a candidate
        # carrying its demoted name. Confidence is no longer this layer's
        # business — the mutation's floor decided it upstream.
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        candidates, skipped = demote_candidates(store, ["mod:helper"])
        assert skipped == []
        assert [(c.symbol_id, c.new_name) for c in candidates] == [
            ("mod:helper", "_helper")
        ]

    def test_hierarchy_unsafe_override_pair(self, indexed_project):
        src = (
            "class Base:\n"
            "    def run(self):\n"
            "        pass\n"
            "\n"
            "\n"
            "class Sub(Base):\n"
            "    def run(self):\n"
            "        pass\n"
        )
        _, store = indexed_project({"mod.py": src})
        candidates, skipped = demote_candidates(
            store, ["mod:Sub.run", "mod:Base.run"]
        )
        assert candidates == []
        reasons = _skip_reasons(skipped)
        assert reasons == {
            "mod:Sub.run": "hierarchy-unsafe",
            "mod:Base.run": "hierarchy-unsafe",
        }
        details = {entry.symbol_id: entry.detail for entry in skipped}
        assert "overrides" in details["mod:Sub.run"]
        assert "overridden by" in details["mod:Base.run"]

    def test_hierarchy_unsafe_unknown_external_base(self, indexed_project):
        src = (
            "import os\n"
            "\n"
            "\n"
            "class Walker(os.PathLike):\n"
            "    def render(self):\n"
            "        pass\n"
        )
        _, store = indexed_project({"mod.py": src})
        candidates, skipped = demote_candidates(store, ["mod:Walker.render"])
        assert candidates == []
        assert _skip_reasons(skipped) == {"mod:Walker.render": "hierarchy-unsafe"}
        assert "mro unknown" in skipped[0].detail

    def test_method_on_baseless_class_is_a_candidate(self, indexed_project):
        src = "class Solo:\n    def helper(self):\n        pass\n"
        _, store = indexed_project({"mod.py": src})
        candidates, skipped = demote_candidates(store, ["mod:Solo.helper"])
        assert skipped == []
        assert [c.new_name for c in candidates] == ["_helper"]

    def test_protected_public_api_in_library_mode(self, indexed_project):
        project, store = indexed_project(BARREL_FILES)
        (project / "pyproject.toml").write_text(LIBRARY_PYPROJECT)
        candidates, skipped = demote_candidates(store, ["pkg.mod:helper"])
        assert candidates == []
        assert _skip_reasons(skipped) == {"pkg.mod:helper": "protected-public-api"}
        assert "library mode" in skipped[0].detail

    def test_library_mode_outside_public_roots_is_allowed(self, indexed_project):
        project, store = indexed_project(BARREL_FILES)
        (project / "pyproject.toml").write_text(
            '[project]\nname = "test"\n\n[tool.pypeeker.visibility]\n'
            'mode = "library"\npublic-roots = ["other"]\n'
        )
        candidates, skipped = demote_candidates(store, ["pkg.mod:helper"])
        assert skipped == []
        assert [c.symbol_id for c in candidates] == ["pkg.mod:helper"]

    def test_barrel_export_sets_include_exports_in_app_mode(self, indexed_project):
        _, store = indexed_project(BARREL_FILES)
        candidates, skipped = demote_candidates(store, ["pkg.mod:helper"])
        assert skipped == []
        (candidate,) = candidates
        assert candidate.include_exports is True
        assert candidate.barrel_packages == ("pkg",)

    def test_plain_symbol_does_not_include_exports(self, indexed_project):
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        (candidate,), _ = demote_candidates(store, ["mod:helper"])
        assert candidate.include_exports is False
        assert candidate.barrel_packages == ()

    def test_name_collision_with_existing_private_name(self, indexed_project):
        src = "def helper():\n    pass\n\n\ndef _helper():\n    pass\n"
        _, store = indexed_project({"mod.py": src})
        candidates, skipped = demote_candidates(store, ["mod:helper"])
        assert candidates == []
        assert _skip_reasons(skipped) == {"mod:helper": "name-collision"}
        assert "_helper" in skipped[0].detail

    def test_pending_collision_duplicate_submission(self, indexed_project):
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        candidates, skipped = demote_candidates(
            store, ["mod:helper", "mod:helper"]
        )
        # First wins, the later duplicate skips — deterministically.
        assert [c.symbol_id for c in candidates] == ["mod:helper"]
        assert _skip_reasons(skipped) == {"mod:helper": "pending-collision"}

    def test_pending_collision_between_shadowed_definitions(self, indexed_project):
        src = "def helper():\n    pass\n\n\ndef helper():\n    pass\n"
        _, store = indexed_project({"mod.py": src})
        candidates, skipped = demote_candidates(
            store, ["mod:helper", "mod:helper$2"]
        )
        # Two distinct symbols in ONE scope whose demoted names collide:
        # the earlier entry wins, the later skips with the winner named.
        assert [c.symbol_id for c in candidates] == ["mod:helper"]
        assert _skip_reasons(skipped) == {"mod:helper$2": "pending-collision"}
        assert "mod:helper" in skipped[0].detail

    def test_same_name_in_different_scopes_is_fine(self, indexed_project):
        files = {
            "a.py": "def helper():\n    pass\n",
            "b.py": "def helper():\n    pass\n",
        }
        _, store = indexed_project(files)
        candidates, skipped = demote_candidates(store, ["a:helper", "b:helper"])
        assert skipped == []
        assert sorted(c.symbol_id for c in candidates) == ["a:helper", "b:helper"]

    def test_input_order_is_preserved(self, indexed_project):
        files = {
            "a.py": "def helper():\n    pass\n",
            "b.py": "def assist():\n    pass\n",
        }
        _, store = indexed_project(files)
        candidates, _ = demote_candidates(store, ["b:assist", "a:helper"])
        assert [c.symbol_id for c in candidates] == ["b:assist", "a:helper"]


# ---------------------------------------------------------------------------
# End-to-end batch demotion over a fixture package
# ---------------------------------------------------------------------------

FIXTURE_PACKAGE = {
    "pkg/__init__.py": "from pkg.core import exported\n",
    "pkg/core.py": (
        "def plain():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def exported():\n"
        "    return 2\n"
        "\n"
        "\n"
        "class Base:\n"
        "    def render(self):\n"
        "        return 0\n"
    ),
    "pkg/sub.py": (
        "from pkg.core import Base\n"
        "\n"
        "\n"
        "class Child(Base):\n"
        "    def render(self):\n"
        "        return 1\n"
    ),
    "app.py": (
        "from pkg import exported\n"
        "from pkg.core import plain\n"
        "\n"
        "print(plain(), exported())\n"
    ),
}

FIXTURE_TARGETS = [
    _demote(symbol_id)
    for symbol_id in ("pkg.core:plain", "pkg.core:exported", "pkg.sub:Child.render")
]


def _non_builtin_unresolved(store: IndexStore) -> set[tuple[str, str]]:
    """(file, resolved-id) pairs whose resolution lands on no project symbol.

    Built fresh from the store's current indexes; comparing the set before
    and after an applied demotion asserts the rename introduced no new
    unresolved references.
    """
    indexes = [
        index
        for path in store.list_indexed_files()
        if (index := store.load(path)) is not None
    ]
    known = {s.symbol_id for index in indexes for s in index.symbols}
    resolver = CrossModuleResolver(indexes)
    dangling: set[tuple[str, str]] = set()
    for index in indexes:
        for ref in index.references:
            resolved = resolver.resolve_reference(ref)
            if resolved not in known and not is_builtin(resolved):
                dangling.add((index.file_path, resolved))
    return dangling


class TestPlanPrivatizeEndToEnd:
    def test_batch_demotion_applies_and_rolls_back(
        self, indexed_project, transaction_store
    ):
        project, store = indexed_project(FIXTURE_PACKAGE)
        originals = {
            name: (project / name).read_bytes() for name in FIXTURE_PACKAGE
        }
        unresolved_before = _non_builtin_unresolved(store)

        outcome = plan_privatize(store, transaction_store, FIXTURE_TARGETS)

        # The override method was skipped with a hierarchy reason; the other
        # two executed into ONE flattened transaction.
        assert _skip_reasons(outcome.skipped) == {
            "pkg.sub:Child.render": "hierarchy-unsafe"
        }
        assert outcome.dropped == ()
        assert sorted(e.symbol_id for e in outcome.executed) == [
            "pkg.core:exported",
            "pkg.core:plain",
        ]
        assert outcome.summary is not None
        assert outcome.summary.operation == PRIVATIZE_OPERATION
        assert sorted(outcome.summary.files_affected) == [
            "app.py",
            "pkg/__init__.py",
            "pkg/core.py",
        ]
        assert any("barrel-exported" in w for w in outcome.warnings)

        # Planning never touches the real tree.
        for name, content in originals.items():
            assert (project / name).read_bytes() == content
        loaded = transaction_store.load(outcome.summary.tx_id)
        header, edits, file_rename = loaded.header, loaded.edits, loaded.file_rename
        assert header.operation == PRIVATIZE_OPERATION
        assert file_rename is None
        assert len(edits) == outcome.summary.edit_count

        result = TransactionApplier(store, transaction_store).apply(
            outcome.summary.tx_id
        )
        assert result["status"] == "applied"
        assert result["files_reindex_failed"] == []

        core = (project / "pkg/core.py").read_text()
        assert "def _plain():" in core
        assert "def _exported():" in core
        assert "class Base:" in core  # the skipped hierarchy stayed put
        # Barrel rewrite: the __init__ re-export switched to the private name.
        assert (
            project / "pkg/__init__.py"
        ).read_text() == "from pkg.core import _exported\n"
        assert (project / "app.py").read_text() == (
            "from pkg import _exported\n"
            "from pkg.core import _plain\n"
            "\n"
            "print(_plain(), _exported())\n"
        )
        sub = (project / "pkg/sub.py").read_text()
        assert "def render(self):" in sub  # override untouched

        # The demoted tree is still valid Python and re-indexes without any
        # new unresolved references.
        for name in FIXTURE_PACKAGE:
            compile((project / name).read_text(), name, "exec")
        assert _non_builtin_unresolved(store) <= unresolved_before

        rollback = TransactionApplier(store, transaction_store).rollback(
            outcome.summary.tx_id
        )
        assert rollback["status"] == "rolled_back"
        for name, content in originals.items():
            assert (project / name).read_bytes() == content

    def test_demoted_names_resolve_after_apply(
        self, indexed_project, transaction_store
    ):
        project, store = indexed_project(FIXTURE_PACKAGE)
        outcome = plan_privatize(store, transaction_store, FIXTURE_TARGETS)
        TransactionApplier(store, transaction_store).apply(outcome.summary.tx_id)
        engine = SemanticQueryEngine(store)
        assert [s.symbol_id for s in engine.find_symbol("pkg.core:_plain")]
        importers = engine.find_importers("pkg.core:_exported")
        assert "pkg/__init__.py" in {
            imp.location.file_path for imp in importers
        }

    def test_dunder_all_entries_follow_the_demotion(
        self, indexed_project, transaction_store
    ):
        files = {
            "pkg/__init__.py": (
                "from pkg.mod import helper\n\n__all__ = ['helper', 'other']\n"
            ),
            "pkg/mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "\n"
                '__all__ = ["helper"]\n'
            ),
            "app.py": "from pkg import helper\n\nhelper()\n",
        }
        project, store = indexed_project(files)
        outcome = plan_privatize(store, transaction_store, [_demote("pkg.mod:helper")])
        assert outcome.summary is not None
        TransactionApplier(store, transaction_store).apply(outcome.summary.tx_id)
        # Both the barrel's and the defining module's __all__ entries follow
        # the rename (the import lines now bind the private name).
        assert (project / "pkg/__init__.py").read_text() == (
            "from pkg.mod import _helper\n\n__all__ = ['_helper', 'other']\n"
        )
        assert (project / "pkg/mod.py").read_text() == (
            "def _helper():\n"
            "    return 1\n"
            "\n"
            "\n"
            '__all__ = ["_helper"]\n'
        )

    def test_all_skipped_yields_no_transaction(
        self, indexed_project, transaction_store
    ):
        # Nothing survives the pre-filter, so there is nothing to flatten: no
        # summary, no executed rows, and — the part worth asserting — no
        # transaction persisted for a run that changed nothing.
        _, store = indexed_project(
            {"mod.py": "def target():\n    pass\n\n\ndef _target():\n    pass\n"}
        )
        outcome = plan_privatize(
            store, transaction_store, [_demote("mod:nope"), _demote("mod:target")]
        )
        assert outcome.summary is None
        assert outcome.executed == []
        assert _skip_reasons(outcome.skipped) == {
            "mod:nope": "not-found",
            "mod:target": "name-collision",
        }
        assert transaction_store.list() == []

    def test_a_skipped_symbol_leaves_the_others_and_the_tree_alone(
        self, indexed_project, transaction_store
    ):
        # One nominated symbol is refused by the pre-filter while another is
        # planned: the transaction carries only the survivor, and the refused
        # symbol's bytes are untouched after apply.
        files = {
            "mod.py": (
                "def safe():\n    pass\n\n\n"
                "def held():\n    pass\n\n\n"
                "def _held():\n    pass\n"
            ),
        }
        project, store = indexed_project(files)
        outcome = plan_privatize(
            store, transaction_store, [_demote("mod:safe"), _demote("mod:held")]
        )
        assert _skip_reasons(outcome.skipped) == {"mod:held": "name-collision"}
        TransactionApplier(store, transaction_store).apply(outcome.summary.tx_id)
        content = (project / "mod.py").read_text()
        assert "def _safe():" in content
        assert "def held():" in content  # the refused symbol is left alone
