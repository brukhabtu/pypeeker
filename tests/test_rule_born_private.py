"""Tests for the builtin born-private ratchet rule (check/builtin/born_private).

Project-scoped, opt-in, self-seeding: the first run records every current
public symbol id under the baseline file's "symbols" namespace and reports
nothing; later runs flag public symbols absent from that record whose
observed references are all module-local. Legacy symbols are never
relitigated, and the rule never auto-extends the baseline after seeding.
"""

from __future__ import annotations

import json

import pytest

from pypeeker.check.baseline import (
    baseline_path,
    has_symbol_baseline,
    load_baseline,
    load_symbol_baseline,
    write_baseline,
    write_symbol_baseline,
)
from pypeeker.check.builtin.born_private import BORN_PRIVATE, _born_private as born_private
from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import get_project_rule
from pypeeker.models.capabilities import Confidence

SEED_FILES = {
    "pkg/lib.py": "def legacy():\n    return 1\n\nlegacy()\n",
    "pkg/app.py": "x = 1\n",
}
"""A project with one legacy module-local public symbol (pkg.lib:legacy)."""

NEW_LOCAL = {"pkg/fresh.py": "def fresh():\n    return 1\n"}
"""A later-added module whose public symbol has no cross-module use."""


@pytest.fixture
def run_rule(indexed_project):
    """Index ``files`` (cumulative across calls — same tmp project), build a
    CheckContext, and run born-private. The baseline file written by the
    first call persists into later calls, simulating a growing project."""

    def _run(files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        return born_private(CheckContext(store, indexes), options or {})

    return _run


# ── registration / opt-in ───────────────────────────────────────────────────


def test_registered_as_project_rule():
    # Importing the module above self-registers it via @register_rule.
    assert get_project_rule(BORN_PRIVATE) is not None


def test_not_in_default_rules():
    # Available but opt-in: pypeeker's own config must not enable it.
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert BORN_PRIVATE not in data["tool"]["pypeeker"]["rules"]


# ── seeding ─────────────────────────────────────────────────────────────────


class TestSeeding:
    def test_first_run_is_silent_and_seeds_symbols_namespace(
        self, run_rule, tmp_path
    ):
        assert run_rule(dict(SEED_FILES)) == []
        path = baseline_path(tmp_path)
        assert has_symbol_baseline(path)
        assert "pkg.lib:legacy" in load_symbol_baseline(path)

    def test_seeded_empty_project_counts_as_seeded(self, run_rule, tmp_path):
        # No public symbols at seed time -> "symbols": []. That must read as
        # "already seeded", so the next public symbol is flagged rather than
        # swallowed by a second silent seed.
        assert run_rule({"pkg/app.py": "x = 1\n"}) == []
        path = baseline_path(tmp_path)
        assert has_symbol_baseline(path)
        assert load_symbol_baseline(path) == set()
        found = run_rule(dict(NEW_LOCAL))
        assert any("'fresh'" in v.message for v in found)

    def test_subsequent_runs_do_not_rewrite_the_baseline(
        self, run_rule, tmp_path
    ):
        run_rule(dict(SEED_FILES))
        path = baseline_path(tmp_path)
        seeded = path.read_text()
        # Two more runs, one of them flagging: no auto-extend, no rewrite.
        run_rule(dict(NEW_LOCAL))
        assert run_rule({}) != []
        assert path.read_text() == seeded


# ── ratchet semantics ───────────────────────────────────────────────────────


class TestRatchet:
    def test_new_module_local_public_symbol_flagged(self, run_rule):
        run_rule(dict(SEED_FILES))
        found = run_rule(dict(NEW_LOCAL))
        flagged = [v for v in found if "'fresh'" in v.message]
        assert len(flagged) == 1
        v = flagged[0]
        assert v.rule == BORN_PRIVATE
        assert v.message == (
            "newly public 'fresh' is only used within its module — make it "
            "_fresh or record it (`check --update-baseline`)"
        )
        assert v.file_path == "pkg/fresh.py"
        assert v.line == 1  # def line, 1-indexed
        assert v.confidence is Confidence.DECLARED

    def test_new_symbol_with_cross_module_use_passes(self, run_rule):
        run_rule(dict(SEED_FILES))
        found = run_rule({
            "pkg/feat.py": "def feature():\n    return 1\n",
            "pkg/use.py": "from pkg.feat import feature\n\nfeature()\n",
        })
        assert not any("'feature'" in v.message for v in found)

    def test_legacy_over_exposed_symbol_untouched(self, run_rule):
        # pkg.lib:legacy is module-local (over-exposed-module-symbol would
        # flag it) but was public at seed time: never relitigated.
        run_rule(dict(SEED_FILES))
        assert run_rule({}) == []

    def test_new_protected_symbol_not_flagged(self, run_rule):
        run_rule(dict(SEED_FILES))
        found = run_rule(
            {"pkg/fresh.py": "def _fresh():\n    return 1\n"}
        )
        assert found == []

    def test_dynamic_access_module_finding_is_heuristic(self, run_rule):
        run_rule(dict(SEED_FILES))
        found = run_rule({
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
    def _new_symbol_msgs(self, run_rule, files, options=None):
        run_rule(dict(SEED_FILES), options)
        return {v.message for v in run_rule(files, options)}

    def test_allow_decorators_exempts_registry_symbols(self, run_rule):
        files = {
            "pkg/handlers.py": (
                "def register(f):\n    return f\n\n"
                "@register\ndef handler():\n    return 1\n"
            ),
        }
        msgs = self._new_symbol_msgs(
            run_rule, files, {"allow-decorators": ["register"]}
        )
        assert not any("'handler'" in m for m in msgs)
        # register itself is new, undecorated, module-local: still flagged.
        assert any("'register'" in m for m in msgs)

    def test_barrel_exported_symbol_exempt(self, run_rule):
        msgs = self._new_symbol_msgs(
            run_rule,
            {
                "pkg/widgets.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.widgets import Widget\n",
            },
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_library_mode_public_root_exempt(self, run_rule):
        msgs = self._new_symbol_msgs(
            run_rule,
            {
                "pkg/widgets.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.widgets import Widget\n",
            },
            {"visibility": {"mode": "library", "public-roots": ["pkg"]}},
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_main_dunder_and_dunder_main_file_exempt(self, run_rule):
        msgs = self._new_symbol_msgs(
            run_rule,
            {
                "pkg/cli.py": (
                    "def main():\n    return 0\n\n"
                    "def __getattr__(name):\n    return 1\n"
                ),
                "pkg/__main__.py": "def entry():\n    return 0\n",
            },
        )
        assert msgs == set()

    def test_allow_pattern_suppresses(self, run_rule):
        msgs = self._new_symbol_msgs(
            run_rule, dict(NEW_LOCAL), {"allow": ["pkg.fresh:fresh"]}
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
        violation = Violation(
            file_path="src/m.py",
            line=3,
            rule="require-docstrings",
            message="public function 'foo' has no docstring",
        )
        counts = write_baseline(path, [violation])
        write_symbol_baseline(path, {"pkg.mod:sym"})
        assert load_baseline(path) == counts  # symbols write kept violations
        write_baseline(path, [violation, violation])
        # ...and the violations rewrite kept the symbols namespace.
        assert load_symbol_baseline(path) == {"pkg.mod:sym"}

    def test_rule_seed_preserves_violations_namespace(
        self, run_rule, tmp_path
    ):
        path = baseline_path(tmp_path)
        counts = write_baseline(
            path,
            [
                Violation(
                    file_path="pkg/lib.py",
                    line=1,
                    rule="require-docstrings",
                    message="public function 'legacy' has no docstring",
                )
            ],
        )
        assert run_rule(dict(SEED_FILES)) == []  # seeds "symbols"
        data = json.loads(path.read_text())
        assert data["violations"] == counts
        assert "pkg.lib:legacy" in data["symbols"]
