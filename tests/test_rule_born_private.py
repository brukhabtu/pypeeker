"""Tests for the builtin born-private ratchet rule (check/builtin/BORN_PRIVATE).

Project-scoped, opt-in, self-seeding: the first run records every current
public symbol id under the baseline file's "symbols" namespace and reports
nothing; later runs flag public symbols absent from that record whose
observed references are all module-local. Legacy symbols are never
relitigated, and the rule never auto-extends the baseline after seeding.
"""

from __future__ import annotations

import json

import pytest

from pypeeker.app.check_run import _seed_born_private
from pypeeker.dsl import PROJECT_VISIBILITY_KEY, RULES, Finding
from pypeeker.models import Confidence
from pypeeker.storage import (
    baseline_path,
    has_symbol_baseline,
    load_baseline,
    load_symbol_baseline,
    write_baseline,
    write_symbol_baseline,
)
from tests.conftest import run_dsl_rule_on_store

BORN_PRIVATE = "born-private"


@pytest.fixture
def run_born_private(indexed_project, tmp_path):
    """Index ``files``, arm the ratchet, then run the rule -> findings.

    The frozen rule seeded itself mid-run: it wrote the baseline's ``symbols``
    namespace and returned ``[]`` on an unseeded project. The ported rule does
    not write at all — :func:`pypeeker.app.check_run._seed_born_private` owns
    that write now, because ``app`` is the layer that owns ``.pypeeker/``
    artifacts. So the two halves are composed here in the order a real
    ``pypeeker check`` composes them, and every ratchet scenario below still
    reads end to end. Indexing is cumulative across calls within one test.
    """

    def _run(files, options=None):
        _, store = indexed_project(files)
        _seed_born_private(
            tmp_path, store, (), {BORN_PRIVATE: options or {}}, reseed=False
        )
        return run_dsl_rule_on_store(BORN_PRIVATE, store, options)

    return _run

SEED_FILES = {
    "pkg/lib.py": "def legacy():\n    return 1\n\nlegacy()\n",
    "pkg/app.py": "x = 1\n",
}
"""A project with one legacy module-local public symbol (pkg.lib:legacy)."""

NEW_LOCAL = {"pkg/fresh.py": "def fresh():\n    return 1\n"}
"""A later-added module whose public symbol has no cross-module use."""


# ── registration / opt-in ───────────────────────────────────────────────────


def test_registered_as_project_rule():
    # Importing the module above self-registers it via @register_rule.
    assert BORN_PRIVATE in RULES


def test_not_in_default_rules():
    # Available but not gated on pypeeker: born-private is intrinsically
    # prospective (needs a stored seed), a poor fit for a baseline-free gate.
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert BORN_PRIVATE not in data["tool"]["pypeeker"]["rules"]


# ── seeding ─────────────────────────────────────────────────────────────────


class TestSeeding:
    def test_first_run_is_silent_and_seeds_symbols_namespace(
        self, run_born_private, tmp_path
    ):
        assert run_born_private(dict(SEED_FILES)) == []
        path = baseline_path(tmp_path)
        assert has_symbol_baseline(path)
        assert "pkg.lib:legacy" in load_symbol_baseline(path)

    def test_seeded_empty_project_counts_as_seeded(self, run_born_private, tmp_path):
        # No public symbols at seed time -> "symbols": []. That must read as
        # "already seeded", so the next public symbol is flagged rather than
        # swallowed by a second silent seed.
        assert run_born_private({"pkg/app.py": "x = 1\n"}) == []
        path = baseline_path(tmp_path)
        assert has_symbol_baseline(path)
        assert load_symbol_baseline(path) == set()
        found = run_born_private(dict(NEW_LOCAL))
        assert any("'fresh'" in v.message for v in found)

    def test_subsequent_runs_do_not_rewrite_the_baseline(
        self, run_born_private, tmp_path
    ):
        run_born_private(dict(SEED_FILES))
        path = baseline_path(tmp_path)
        seeded = path.read_text()
        # Two more runs, one of them flagging: no auto-extend, no rewrite.
        run_born_private(dict(NEW_LOCAL))
        assert run_born_private({}) != []
        assert path.read_text() == seeded


# ── ratchet semantics ───────────────────────────────────────────────────────


class TestRatchet:
    def test_new_module_local_public_symbol_flagged(self, run_born_private):
        run_born_private(dict(SEED_FILES))
        found = run_born_private(dict(NEW_LOCAL))
        flagged = [v for v in found if "'fresh'" in v.message]
        assert len(flagged) == 1
        v = flagged[0]
        assert v.rule == BORN_PRIVATE
        assert v.message == (
            "newly public 'fresh' is only used within its module — make it "
            "_fresh or record it (`check --update-baseline`)"
        )
        assert v.path == "pkg/fresh.py"
        assert v.line == 1  # def line, 1-indexed
        assert v.confidence is Confidence.DECLARED

    def test_new_symbol_with_cross_module_use_passes(self, run_born_private):
        run_born_private(dict(SEED_FILES))
        found = run_born_private({
            "pkg/feat.py": "def feature():\n    return 1\n",
            "pkg/use.py": "from pkg.feat import feature\n\nfeature()\n",
        })
        assert not any("'feature'" in v.message for v in found)

    def test_legacy_over_exposed_symbol_untouched(self, run_born_private):
        # pkg.lib:legacy is module-local (over-exposed-module-symbol would
        # flag it) but was public at seed time: never relitigated.
        run_born_private(dict(SEED_FILES))
        assert run_born_private({}) == []

    def test_new_protected_symbol_not_flagged(self, run_born_private):
        run_born_private(dict(SEED_FILES))
        found = run_born_private(
            {"pkg/fresh.py": "def _fresh():\n    return 1\n"}
        )
        assert found == []

    def test_dynamic_access_module_finding_is_heuristic(self, run_born_private):
        run_born_private(dict(SEED_FILES))
        found = run_born_private({
            "pkg/dyn.py": (
                "def fresh():\n    return 1\n\n"
                "value = getattr(object, 'x', None)\n"
            )
        })
        flagged = [v for v in found if "'fresh'" in v.message]
        assert flagged
        assert all(v.confidence is Confidence.HEURISTIC for v in flagged)


# ── exemptions (same set as over-exposed-module-symbol) ─────────────────────


class TestExemptions:
    def _new_symbol_msgs(self, run_born_private, files, options=None):
        run_born_private(dict(SEED_FILES), options)
        return {v.message for v in run_born_private(files, options)}

    def test_allow_decorators_exempts_registry_symbols(self, run_born_private):
        files = {
            "pkg/handlers.py": (
                "def register(f):\n    return f\n\n"
                "@register\ndef handler():\n    return 1\n"
            ),
        }
        msgs = self._new_symbol_msgs(
            run_born_private, files, {"allow-decorators": ["register"]}
        )
        assert not any("'handler'" in m for m in msgs)
        # register itself is new, undecorated, module-local: still flagged.
        assert any("'register'" in m for m in msgs)

    def test_barrel_exported_symbol_exempt(self, run_born_private):
        msgs = self._new_symbol_msgs(
            run_born_private,
            {
                "pkg/widgets.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.widgets import Widget\n",
            },
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_library_mode_public_root_exempt(self, run_born_private):
        msgs = self._new_symbol_msgs(
            run_born_private,
            {
                "pkg/widgets.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.widgets import Widget\n",
            },
            {PROJECT_VISIBILITY_KEY: {"mode": "library", "public-roots": ["pkg"]}},
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_main_dunder_and_dunder_main_file_exempt(self, run_born_private):
        msgs = self._new_symbol_msgs(
            run_born_private,
            {
                "pkg/cli.py": (
                    "def main():\n    return 0\n\n"
                    "def __getattr__(name):\n    return 1\n"
                ),
                "pkg/__main__.py": "def entry():\n    return 0\n",
            },
        )
        assert msgs == set()

    def test_allow_pattern_suppresses(self, run_born_private):
        msgs = self._new_symbol_msgs(
            run_born_private, dict(NEW_LOCAL), {"allow": ["pkg.fresh:fresh"]}
        )
        assert not any("'fresh'" in m for m in msgs)


# ── baseline file: symbols namespace round-trip ─────────────────────────────


class TestSymbolBaselineStorage:
    def test_load_is_empty_when_file_or_namespace_absent(self, tmp_path):
        path = tmp_path / "check-baseline.json"
        assert load_symbol_baseline(path) == set()
        assert not has_symbol_baseline(path)
        write_baseline(path, [])  # violations namespace only
        assert load_symbol_baseline(path) == set()
        assert not has_symbol_baseline(path)

    def test_write_is_sorted_and_round_trips(self, tmp_path):
        path = tmp_path / "check-baseline.json"
        recorded = write_symbol_baseline(path, {"b.mod:y", "a.mod:x"})
        assert recorded == ["a.mod:x", "b.mod:y"]
        assert json.loads(path.read_text())["symbols"] == recorded
        assert load_symbol_baseline(path) == {"a.mod:x", "b.mod:y"}
        assert has_symbol_baseline(path)

    def test_namespaces_preserve_each_other(self, tmp_path):
        path = tmp_path / "check-baseline.json"
        violation = Finding(
            rule="require-docstrings",
            path="src/m.py",
            line=3,
            message="public function 'foo' has no docstring",
            confidence=Confidence.DECLARED,
            anchor_id="src.m:foo",
        )
        counts = write_baseline(path, [violation])
        write_symbol_baseline(path, {"pkg.mod:sym"})
        assert load_baseline(path) == counts  # symbols write kept violations
        write_baseline(path, [violation, violation])
        # ...and the violations rewrite kept the symbols namespace.
        assert load_symbol_baseline(path) == {"pkg.mod:sym"}

    def test_rule_seed_preserves_violations_namespace(
        self, run_born_private, tmp_path
    ):
        path = baseline_path(tmp_path)
        counts = write_baseline(
            path,
            [
                Finding(
                    rule="require-docstrings",
                    path="pkg/lib.py",
                    line=1,
                    message="public function 'legacy' has no docstring",
                    confidence=Confidence.DECLARED,
                    anchor_id="pkg.lib:legacy",
                )
            ],
        )
        assert run_born_private(dict(SEED_FILES)) == []  # seeds "symbols"
        data = json.loads(path.read_text())
        assert data["violations"] == counts
        assert "pkg.lib:legacy" in data["symbols"]
