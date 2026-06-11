"""Tests for the pure-decorator-contracts builtin rule."""

from __future__ import annotations

from pypeeker.check import CheckContext
from pypeeker.check.builtin.pure_decorator_contracts import (
    PURE_DECORATOR_CONTRACTS,
    pure_decorator_contracts,
)
from pypeeker.check.rules import get_project_rule

IMPURE_LRU_CACHE_SRC = (
    "import time\n"
    "from functools import lru_cache\n"
    "\n"
    "@lru_cache(maxsize=None)\n"
    "def now_ish():\n"
    "    return time.time()\n"
)

IMPURE_PROPERTY_SRC = (
    "class Report:\n"
    "    @property\n"
    "    def body(self):\n"
    "        print('rendering')\n"
    "        return 'body'\n"
)

PURE_CACHE_SRC = (
    "from functools import cache\n"
    "\n"
    "@cache\n"
    "def double(x):\n"
    "    return x * 2\n"
)


class TestPureDecoratorContracts:
    def _run(self, indexed_project, files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        context = CheckContext(store, indexes)
        return pure_decorator_contracts(context, options or {})

    # ── decorator contract ──────────────────────────────────────────────

    def test_lru_cache_on_impure_function_flagged(self, indexed_project):
        violations = self._run(
            indexed_project, {"pkg/mod.py": IMPURE_LRU_CACHE_SRC}
        )
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == PURE_DECORATOR_CONTRACTS
        assert "'pkg.mod:now_ish'" in v.message
        assert "@lru_cache" in v.message
        assert "time.time" in v.message
        assert v.line == 5  # def line, 1-indexed

    def test_impure_property_flagged(self, indexed_project):
        violations = self._run(
            indexed_project, {"pkg/mod.py": IMPURE_PROPERTY_SRC}
        )
        assert len(violations) == 1
        assert "'pkg.mod:Report.body'" in violations[0].message
        assert "@property" in violations[0].message
        assert "print" in violations[0].message

    def test_pure_cache_not_flagged(self, indexed_project):
        violations = self._run(indexed_project, {"pkg/mod.py": PURE_CACHE_SRC})
        assert violations == []

    def test_functools_prefixed_decorator_matched(self, indexed_project):
        src = (
            "import functools\n"
            "import time\n"
            "\n"
            "@functools.cache\n"
            "def stamp():\n"
            "    return time.time()\n"
        )
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "@functools.cache" in violations[0].message

    def test_impure_cached_property_flagged(self, indexed_project):
        src = (
            "from functools import cached_property\n"
            "\n"
            "class Config:\n"
            "    @cached_property\n"
            "    def text(self):\n"
            "        return open('cfg.txt').read()\n"
        )
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "@cached_property" in violations[0].message
        assert "open" in violations[0].message

    # ── dunder contract ─────────────────────────────────────────────────

    def test_impure_repr_flagged(self, indexed_project):
        src = (
            "class Widget:\n"
            "    def __repr__(self):\n"
            "        print('repr called')\n"
            "        return 'Widget()'\n"
        )
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "'pkg.mod:Widget.__repr__'" in violations[0].message
        assert "__repr__ purity contract" in violations[0].message
        assert violations[0].line == 2

    def test_pure_eq_not_flagged(self, indexed_project):
        src = (
            "class Point:\n"
            "    def __eq__(self, other):\n"
            "        return self.x == other.x\n"
        )
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        assert violations == []

    def test_non_contract_dunder_not_flagged(self, indexed_project):
        src = (
            "class Resource:\n"
            "    def __enter__(self):\n"
            "        print('opening')\n"
            "        return self\n"
        )
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        assert violations == []

    # ── scope: this rule is not no-impure-functions ─────────────────────

    def test_undecorated_impure_non_dunder_not_flagged(self, indexed_project):
        src = "def shout(x):\n    print(x)\n    return x\n"
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        assert violations == []

    # ── options ─────────────────────────────────────────────────────────

    def test_allow_suppresses_by_symbol_id_pattern(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"pkg/mod.py": IMPURE_LRU_CACHE_SRC},
            {"allow": ["pkg.mod:now_ish"]},
        )
        assert violations == []

    def test_allow_supports_fnmatch_wildcards(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"pkg/mod.py": IMPURE_LRU_CACHE_SRC},
            {"allow": ["pkg.*:now_*"]},
        )
        assert violations == []

    def test_decorators_option_overrides_defaults(self, indexed_project):
        # With only 'memoize' configured, the default lru_cache no longer
        # carries a contract, and a custom @memoize decorator does.
        src = (
            "import time\n"
            "\n"
            "def memoize(f):\n"
            "    return f\n"
            "\n"
            "@memoize\n"
            "def stamp():\n"
            "    return time.time()\n"
        )
        violations = self._run(
            indexed_project,
            {"pkg/mod.py": src, "pkg/other.py": IMPURE_LRU_CACHE_SRC},
            {"decorators": ["memoize"]},
        )
        assert len(violations) == 1
        assert "@memoize" in violations[0].message
        assert not any("now_ish" in v.message for v in violations)

    def test_dunders_option_overrides_defaults(self, indexed_project):
        src = (
            "class Box:\n"
            "    def __contains__(self, item):\n"
            "        print('checking')\n"
            "        return True\n"
            "\n"
            "    def __repr__(self):\n"
            "        print('repr')\n"
            "        return 'Box()'\n"
        )
        violations = self._run(
            indexed_project, {"pkg/mod.py": src}, {"dunders": ["__contains__"]}
        )
        assert len(violations) == 1
        assert "__contains__" in violations[0].message
        assert not any("__repr__" in v.message for v in violations)

    # ── transitive impurity through the contract ────────────────────────

    def test_transitively_impure_cached_function_flagged(self, indexed_project):
        src = (
            "from functools import cache\n"
            "\n"
            "def helper(x):\n"
            "    print(x)\n"
            "    return x\n"
            "\n"
            "@cache\n"
            "def wrapped(x):\n"
            "    return helper(x)\n"
        )
        violations = self._run(indexed_project, {"pkg/mod.py": src})
        # Only the decorated function is in scope; the impure helper is
        # no-impure-functions territory.
        assert [v.message.split("'")[1] for v in violations] == [
            "pkg.mod:wrapped"
        ]

    # ── registration / opt-in ───────────────────────────────────────────

    def test_registered_as_project_rule(self):
        assert get_project_rule(PURE_DECORATOR_CONTRACTS) is pure_decorator_contracts

    def test_not_in_default_rules(self):
        # pure-decorator-contracts is available but opt-in. Anchored to this
        # file (not cwd) so the assertion survives cwd-changing tests.
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        assert PURE_DECORATOR_CONTRACTS not in data["tool"]["pypeeker"]["rules"]
