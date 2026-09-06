"""Tests for :func:`pypeeker.refactor.plan_privatize` (TASK-157, A13).

It takes :class:`~pypeeker.intents.intents.ChangeVisibilityIntent` objects the
DSL's ``DEMOTE`` mutation already decided on. That is the whole shape of the
layer split: three of the pre-filter's eight skip codes were *pointwise*
properties of the row itself (``heuristic-confidence``, ``dunder-or-main``,
``already-private``), and they are now the mutation's confidence floor and its
two preconditions, which refuse those rows before an intent exists. The five
batch- and project-shaped ones — plus the batch-wide ``pending-collision`` —
have no pointwise answer, so they stay here.

Two invariants get pinned that nothing else can see:

* every remaining skip code still fires, and ``pending-collision`` keeps the
  first submission;
* the retired ones do *not* come back as skip rows — a row that should have
  been refused by the mutation raises loudly rather than being planned into
  ``__name``, so a gap in the mutation's guards can never be laundered into a
  silent double-underscore rename.
"""

from __future__ import annotations

import pytest

from pypeeker.intents import ChangeVisibilityIntent
from pypeeker.refactor import TransactionApplier, plan_privatize

LIBRARY_PYPROJECT = (
    '[project]\nname = "test"\n\n[tool.pypeeker.visibility]\nmode = "library"\n'
)

BARREL_FILES = {
    "pkg/__init__.py": '__all__ = ["helper"]\n\nfrom pkg.mod import helper\n',
    "pkg/mod.py": "def helper():\n    return 1\n",
    "app.py": "from pkg import helper\n\nhelper()\n",
}


def _demote(symbol_id: str, origin: str = "over-exposed-module-symbol"):
    """A DSL-shaped demote intent, derived id and all."""
    return ChangeVisibilityIntent(
        f"{origin}:demote:{symbol_id}", symbol_id, "demote"
    )


def _reasons(outcome) -> dict[str, str]:
    return {entry.symbol_id: entry.reason for entry in outcome.skipped}


class TestNonPointwiseSkipsStillFire:
    def test_not_found(self, indexed_project, transaction_store):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        outcome = plan_privatize(
            store, transaction_store, [_demote("mod:nope")]
        )
        assert outcome.summary is None
        assert _reasons(outcome) == {"mod:nope": "not-found"}

    def test_ambiguous(self, indexed_project, transaction_store):
        _, store = indexed_project(
            {"a.py": "def helper():\n    pass\n", "b.py": "def helper():\n    pass\n"}
        )
        outcome = plan_privatize(
            store, transaction_store, [_demote("helper")]
        )
        assert _reasons(outcome) == {"helper": "ambiguous"}

    def test_hierarchy_unsafe(self, indexed_project, transaction_store):
        src = (
            "class Base:\n    def run(self):\n        pass\n\n\n"
            "class Sub(Base):\n    def run(self):\n        pass\n"
        )
        _, store = indexed_project({"mod.py": src})
        outcome = plan_privatize(
            store, transaction_store, [_demote("mod:Sub.run")]
        )
        assert _reasons(outcome) == {"mod:Sub.run": "hierarchy-unsafe"}

    def test_protected_public_api(self, indexed_project, transaction_store):
        project, store = indexed_project(BARREL_FILES)
        (project / "pyproject.toml").write_text(LIBRARY_PYPROJECT)
        outcome = plan_privatize(
            store, transaction_store, [_demote("pkg.mod:helper")]
        )
        assert _reasons(outcome) == {"pkg.mod:helper": "protected-public-api"}

    def test_name_collision(self, indexed_project, transaction_store):
        src = "def helper():\n    pass\n\n\ndef _helper():\n    pass\n"
        _, store = indexed_project({"mod.py": src})
        outcome = plan_privatize(
            store, transaction_store, [_demote("mod:helper")]
        )
        assert _reasons(outcome) == {"mod:helper": "name-collision"}

    def test_pending_collision_keeps_the_first_submission(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        first = _demote("mod:helper", "over-exposed-module-symbol")
        second = _demote("mod:helper", "unused-public-symbol")

        outcome = plan_privatize(store, transaction_store, [first, second])

        assert [e.intent_id for e in outcome.executed] == [first.intent_id]
        assert _reasons(outcome) == {"mod:helper": "pending-collision"}


class TestTheRetiredPointwiseBranches:
    def test_heuristic_confidence_is_never_reported(
        self, indexed_project, transaction_store
    ):
        # A DSL intent carries no confidence string at all: the floor already
        # refused the weakened rows, so nothing here can produce that code.
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        outcome = plan_privatize(
            store, transaction_store, [_demote("mod:helper")]
        )
        assert "heuristic-confidence" not in _reasons(outcome).values()
        assert [e.symbol_id for e in outcome.executed] == ["mod:helper"]

    @pytest.mark.parametrize(
        "src, symbol_id",
        [
            ("def _quiet():\n    pass\n", "mod:_quiet"),
            ("def main():\n    pass\n", "mod:main"),
            ("def __getattr__(name):\n    pass\n", "mod:__getattr__"),
        ],
    )
    def test_a_row_the_mutation_should_have_refused_raises(
        self, indexed_project, transaction_store, src, symbol_id
    ):
        _, store = indexed_project({"mod.py": src})
        with pytest.raises(ValueError, match="preconditions should have refused"):
            plan_privatize(
                store, transaction_store, [_demote(symbol_id)]
            )


class TestEverySkipCodeIsReachable:
    """The five project-shaped skip codes, all reached in one batch.

    Six survive A13 in total; ``pending-collision`` is the sixth and needs two
    submissions of one symbol, so it is asserted on its own above. Eight
    before A13: ``heuristic-confidence``, ``dunder-or-main`` and
    ``already-private`` are gone from this layer entirely — they are
    :data:`pypeeker.dsl.DEMOTE`'s floor and preconditions now, asserted in
    ``tests/test_dsl_terminals.py`` — and the class above proves those rows
    cannot arrive here at all.
    """

    def test_every_skip_code_still_reachable(
        self, indexed_project, transaction_store
    ):
        project, store = indexed_project(
            {
                **BARREL_FILES,
                "mod.py": (
                    "def target():\n    pass\n\n\n"
                    "def _target():\n    pass\n\n\n"
                    "class Base:\n    def run(self):\n        pass\n\n\n"
                    "class Sub(Base):\n    def run(self):\n        pass\n"
                ),
                "a.py": "def dup():\n    pass\n",
                "b.py": "def dup():\n    pass\n",
            }
        )
        (project / "pyproject.toml").write_text(LIBRARY_PYPROJECT)

        outcome = plan_privatize(
            store,
            transaction_store,
            [
                _demote("mod:nope"),
                _demote("dup"),
                _demote("pkg.mod:helper"),
                _demote("mod:target"),
                _demote("mod:Sub.run"),
            ],
        )
        assert _reasons(outcome) == {
            "mod:nope": "not-found",
            "dup": "ambiguous",
            "pkg.mod:helper": "protected-public-api",
            "mod:target": "name-collision",
            "mod:Sub.run": "hierarchy-unsafe",
        }


class TestBatchOfChangeVisibilityIntents:
    def test_barrel_export_is_rewritten_without_any_caller_passing_include_exports(
        self, indexed_project, transaction_store
    ):
        # ChangeVisibilityIntent carries only symbol_id/direction/keep_export/
        # add_export; VisibilityPlanner.plan_demote derives include_exports
        # itself (`bool(barrel_exports) and not keep_export`). Nothing in this
        # test names it, and the __init__ re-export still moves.
        project, store = indexed_project(
            {**BARREL_FILES, "solo.py": "def alone():\n    return 2\n"}
        )
        intents = [_demote("pkg.mod:helper"), _demote("solo:alone")]
        assert not any(hasattr(i, "include_exports") for i in intents)

        outcome = plan_privatize(store, transaction_store, intents)

        assert outcome.summary is not None
        assert sorted(e.symbol_id for e in outcome.executed) == [
            "pkg.mod:helper",
            "solo:alone",
        ]
        assert [e.new_name for e in outcome.executed] == ["_helper", "_alone"]
        assert any("barrel-exported by pkg" in w for w in outcome.warnings)

        TransactionApplier(store, transaction_store).apply(outcome.summary.tx_id)
        init = (project / "pkg" / "__init__.py").read_text()
        assert "from pkg.mod import _helper" in init
        assert '__all__ = ["_helper"]' in init
        assert "from pkg import _helper" in (project / "app.py").read_text()
        assert "def _alone" in (project / "solo.py").read_text()
