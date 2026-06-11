"""Tests for PurityPolicy: the configurable purity denylists (TASK-68).

Covers the ``extended()`` derivation mechanics (extra + allow) and that
``impurities(..., policy=...)`` actually honors a non-default policy, while
the default keeps today's behavior.
"""

from __future__ import annotations

from pypeeker.analysis.purity import (
    DEFAULT_POLICY,
    IMPURE_BUILTINS,
    IO_METHOD_NAMES,
    MODULE_IMPURE_NAMES,
    TYPE_IMPURE_METHODS,
    PurityPolicy,
    impurities,
)


class TestPolicyExtended:
    def test_default_policy_mirrors_module_tables(self):
        assert DEFAULT_POLICY.impure_builtins == IMPURE_BUILTINS
        assert DEFAULT_POLICY.module_impure_names == MODULE_IMPURE_NAMES
        assert DEFAULT_POLICY.io_method_names == IO_METHOD_NAMES
        assert DEFAULT_POLICY.type_impure_methods == TYPE_IMPURE_METHODS

    def test_extra_impure_builtins_added(self):
        policy = DEFAULT_POLICY.extended(extra_impure_builtins=["log"])
        assert "log" in policy.impure_builtins
        # Existing entries are preserved, the default is untouched.
        assert "print" in policy.impure_builtins
        assert "log" not in DEFAULT_POLICY.impure_builtins

    def test_extra_module_impure_added(self):
        policy = DEFAULT_POLICY.extended(extra_module_impure=["mypkg.db.commit"])
        assert "mypkg.db.commit" in policy.module_impure_names
        assert "os.system" in policy.module_impure_names

    def test_extra_io_methods_added_and_tracked(self):
        policy = DEFAULT_POLICY.extended(extra_io_methods=["send_metrics"])
        assert "send_metrics" in policy.io_method_names
        assert "send_metrics" in policy.tracked_method_names

    def test_allow_removes_from_every_denylist(self):
        policy = DEFAULT_POLICY.extended(allow=["print", "os.system", "write"])
        assert "print" not in policy.impure_builtins
        assert "os.system" not in policy.module_impure_names
        assert "write" not in policy.io_method_names
        assert all(
            "write" not in methods
            for methods in policy.type_impure_methods.values()
        )
        assert "write" not in policy.tracked_method_names

    def test_allow_wins_over_extra(self):
        policy = DEFAULT_POLICY.extended(
            extra_impure_builtins=["log"], allow=["log"]
        )
        assert "log" not in policy.impure_builtins

    def test_extended_is_a_new_frozen_value(self):
        policy = DEFAULT_POLICY.extended(allow=["print"])
        assert policy is not DEFAULT_POLICY
        assert "print" in DEFAULT_POLICY.impure_builtins

    def test_plain_construction_defaults_match(self):
        assert PurityPolicy() == DEFAULT_POLICY


class TestImpuritiesWithPolicy:
    def test_extra_builtin_flags_custom_bare_name(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(x):\n    log(x)\n    return x\n"
        })
        # Default policy: 'log' is an unresolved bare name, not impure.
        default = impurities(store, "mod:f")
        assert default is not None and not default
        policy = DEFAULT_POLICY.extended(extra_impure_builtins=["log"])
        found = impurities(store, "mod:f", policy=policy)
        assert found is not None and found
        assert any(getattr(o, "name", None) == "log" for o in found)

    def test_extra_module_name_flags_custom_qualified_call(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "import mypkg\n\ndef f():\n    mypkg.db.commit()\n"
        })
        default = impurities(store, "mod:f")
        assert default is not None and not default
        policy = DEFAULT_POLICY.extended(extra_module_impure=["mypkg.db.commit"])
        found = impurities(store, "mod:f", policy=policy)
        assert found is not None and found

    def test_allow_unflags_default_builtin(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(x):\n    print(x)\n    return x\n"
        })
        # Default policy flags print.
        assert impurities(store, "mod:f")
        policy = DEFAULT_POLICY.extended(allow=["print"])
        found = impurities(store, "mod:f", policy=policy)
        assert found is not None and not found

    def test_policy_applies_transitively(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def helper(x):\n"
                "    print(x)\n\n"
                "def caller(x):\n"
                "    return helper(x)\n"
            )
        })
        # Transitively impure by default; allowing print purifies the chain.
        assert impurities(store, "mod:caller")
        policy = DEFAULT_POLICY.extended(allow=["print"])
        found = impurities(store, "mod:caller", policy=policy)
        assert found is not None and not found

    def test_default_policy_keyword_is_optional(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(a, b):\n    return a + b\n"
        })
        assert impurities(store, "mod:f", policy=DEFAULT_POLICY) == impurities(
            store, "mod:f"
        )
