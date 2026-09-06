"""Tests for :func:`pypeeker.refactor.plan_privatize_intents` (TASK-157, A13).

The additive Phase-A twin of :func:`~pypeeker.refactor.plan_privatize`: it
takes :class:`~pypeeker.intents.intents.ChangeVisibilityIntent` objects the
DSL's ``DEMOTE`` mutation already decided on, so the three *pointwise*
pre-filter branches (``heuristic-confidence``, ``dunder-or-main``,
``already-private``) are turned off and the five batch/project-shaped ones
still run.

Two invariants get pinned here that nothing else can see:

* the five non-pointwise skip codes plus ``pending-collision`` still fire with
  ``pointwise_guards=False``;
* the pointwise ones do *not* come back as skip rows — a row that should have
  been refused by the mutation raises loudly instead of being planned as
  ``__name``.

``plan_privatize`` itself is untouched by A13; ``TestFrozenEntryPointUnchanged``
re-runs the eight-code inventory through the old entry point to prove it.
"""

from __future__ import annotations

import pytest

from pypeeker.intents import ChangeVisibilityIntent
from pypeeker.refactor import TransactionApplier, plan_privatize, plan_privatize_intents
from pypeeker.refactor.privatize import _demote_candidates as demote_candidates

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
        outcome = plan_privatize_intents(
            store, transaction_store, [_demote("mod:nope")]
        )
        assert outcome.summary is None
        assert _reasons(outcome) == {"mod:nope": "not-found"}

    def test_ambiguous(self, indexed_project, transaction_store):
        _, store = indexed_project(
            {"a.py": "def helper():\n    pass\n", "b.py": "def helper():\n    pass\n"}
        )
        outcome = plan_privatize_intents(
            store, transaction_store, [_demote("helper")]
        )
        assert _reasons(outcome) == {"helper": "ambiguous"}

    def test_hierarchy_unsafe(self, indexed_project, transaction_store):
        src = (
            "class Base:\n    def run(self):\n        pass\n\n\n"
            "class Sub(Base):\n    def run(self):\n        pass\n"
        )
        _, store = indexed_project({"mod.py": src})
        outcome = plan_privatize_intents(
            store, transaction_store, [_demote("mod:Sub.run")]
        )
        assert _reasons(outcome) == {"mod:Sub.run": "hierarchy-unsafe"}

    def test_protected_public_api(self, indexed_project, transaction_store):
        project, store = indexed_project(BARREL_FILES)
        (project / "pyproject.toml").write_text(LIBRARY_PYPROJECT)
        outcome = plan_privatize_intents(
            store, transaction_store, [_demote("pkg.mod:helper")]
        )
        assert _reasons(outcome) == {"pkg.mod:helper": "protected-public-api"}

    def test_name_collision(self, indexed_project, transaction_store):
        src = "def helper():\n    pass\n\n\ndef _helper():\n    pass\n"
        _, store = indexed_project({"mod.py": src})
        outcome = plan_privatize_intents(
            store, transaction_store, [_demote("mod:helper")]
        )
        assert _reasons(outcome) == {"mod:helper": "name-collision"}

    def test_pending_collision_keeps_the_first_submission(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        first = _demote("mod:helper", "over-exposed-module-symbol")
        second = _demote("mod:helper", "unused-public-symbol")

        outcome = plan_privatize_intents(store, transaction_store, [first, second])

        assert [e.intent_id for e in outcome.executed] == [first.intent_id]
        assert _reasons(outcome) == {"mod:helper": "pending-collision"}


class TestPointwiseGuardsAreOff:
    def test_heuristic_confidence_is_never_reported(
        self, indexed_project, transaction_store
    ):
        # A DSL intent carries no confidence string at all: the floor already
        # refused the weakened rows, so nothing here can produce that code.
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        outcome = plan_privatize_intents(
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
            plan_privatize_intents(
                store, transaction_store, [_demote(symbol_id)]
            )

    def test_the_pointwise_branches_are_still_live_by_default(
        self, indexed_project
    ):
        # pointwise_guards defaults to True, which is what keeps plan_privatize
        # working unchanged while both entry points exist.
        _, store = indexed_project({"mod.py": "def _quiet():\n    pass\n"})
        candidates, skipped = demote_candidates(store, ["mod:_quiet"])
        assert candidates == []
        assert [s.reason for s in skipped] == ["already-private"]

        candidates, skipped = demote_candidates(
            store, ["mod:_quiet"], pointwise_guards=False
        )
        assert skipped == []
        assert [c.new_name for c in candidates] == ["__quiet"]


class TestFrozenEntryPointUnchanged:
    """``plan_privatize``'s eight skip codes are all still reachable."""

    def test_every_skip_code_still_reachable(
        self, indexed_project, transaction_store
    ):
        project, store = indexed_project(
            {
                **BARREL_FILES,
                "mod.py": (
                    "def _quiet():\n    pass\n\n\n"
                    "def main():\n    pass\n\n\n"
                    "def target():\n    pass\n\n\n"
                    "def _target():\n    pass\n\n\n"
                    "def twin():\n    pass\n\n\n"
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
                "mod:nope",
                "dup",
                "mod:_quiet",
                "mod:main",
                ("mod:twin", "heuristic"),
                "pkg.mod:helper",
                "mod:target",
                "mod:Sub.run",
            ],
        )
        assert _reasons(outcome) == {
            "mod:nope": "not-found",
            "dup": "ambiguous",
            "mod:_quiet": "already-private",
            "mod:main": "dunder-or-main",
            "mod:twin": "heuristic-confidence",
            "pkg.mod:helper": "protected-public-api",
            "mod:target": "name-collision",
            "mod:Sub.run": "hierarchy-unsafe",
        }

    def test_pending_collision_through_the_frozen_entry_point(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def helper():\n    pass\n"})
        outcome = plan_privatize(
            store, transaction_store, ["mod:helper", "mod:helper"]
        )
        assert [e.intent_id for e in outcome.executed] == ["demote:mod:helper"]
        assert _reasons(outcome) == {"mod:helper": "pending-collision"}


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

        outcome = plan_privatize_intents(store, transaction_store, intents)

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
