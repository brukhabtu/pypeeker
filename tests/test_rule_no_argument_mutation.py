"""Tests for the builtin ``no-argument-mutation`` project rule."""

from __future__ import annotations

from pypeeker.dsl import RULES

RULE = "no-argument-mutation"


class TestNoArgumentMutation:
    # ── flagged mutation shapes ─────────────────────────────────────────────

    def test_param_append_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": "def f(items):\n    items.append(1)\n"},
        )
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == RULE
        assert "'pkg.mod:f'" in v.message
        assert "parameter 'items' mutated via append()" in v.message
        assert v.line == 2  # mutation site, 1-indexed

    def test_param_dict_update_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": "def f(d):\n    d.update({})\n"},
        )
        assert len(violations) == 1
        assert "parameter 'd' mutated via update()" in violations[0].message

    def test_param_attribute_write_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": "def f(obj):\n    obj.x = 1\n"},
        )
        assert len(violations) == 1
        assert (
            "parameter 'obj' mutated via attribute write '.x'"
            in violations[0].message
        )

    def test_param_subscript_write_flagged(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": "def f(xs):\n    xs[0] = 1\n"},
        )
        assert len(violations) == 1
        assert (
            "parameter 'xs' mutated via subscript write" in violations[0].message
        )

    def test_nested_chain_mutation_names_chain(self, run_dsl_rule):
        violations = run_dsl_rule(
            RULE,
            {"pkg/mod.py": "def f(cfg):\n    cfg.items.append(1)\n"},
        )
        assert len(violations) == 1
        assert (
            "parameter 'cfg' mutated via items.append()" in violations[0].message
        )

    def test_method_mutating_non_self_param_flagged(self, run_dsl_rule):
        src = (
            "class Sink:\n"
            "    def fill(self, out):\n"
            "        out.append(1)\n"
        )
        violations = run_dsl_rule(RULE, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "parameter 'out' mutated via append()" in violations[0].message

    # ── not flagged ─────────────────────────────────────────────────────────

    def test_local_variable_mutation_not_flagged(self, run_dsl_rule):
        src = (
            "def f():\n"
            "    xs = []\n"
            "    xs.append(1)\n"
            "    xs[0] = 2\n"
            "    return xs\n"
        )
        assert run_dsl_rule(RULE, {"pkg/mod.py": src}) == []

    def test_local_attribute_write_not_flagged(self, run_dsl_rule):
        src = (
            "def f():\n"
            "    obj = make()\n"
            "    obj.x = 1\n"
            "    return obj\n"
        )
        assert run_dsl_rule(RULE, {"pkg/mod.py": src}) == []

    def test_self_and_cls_mutations_not_flagged(self, run_dsl_rule):
        src = (
            "class Box:\n"
            "    def add(self, v):\n"
            "        self.items.append(v)\n"
            "        self.count = 1\n"
            "    @classmethod\n"
            "    def configure(cls, v):\n"
            "        cls.registry.update(v)\n"
            "        cls.default = v\n"
        )
        assert run_dsl_rule(RULE, {"pkg/mod.py": src}) == []

    def test_non_mutator_method_call_not_flagged(self, run_dsl_rule):
        src = "def f(items):\n    return items.copy()\n"
        assert run_dsl_rule(RULE, {"pkg/mod.py": src}) == []

    # ── options ─────────────────────────────────────────────────────────────

    def test_allow_pattern_suppresses_function(self, run_dsl_rule):
        files = {"pkg/mod.py": "def f(items):\n    items.append(1)\n"}
        assert (
            run_dsl_rule(RULE, files, {"allow": ["pkg.mod:f"]}) == []
        )
        assert (
            run_dsl_rule(RULE, files, {"allow": ["pkg.mod:*"]}) == []
        )

    def test_allow_pattern_only_skips_matching_functions(self, run_dsl_rule):
        files = {
            "pkg/mod.py": (
                "def sanctioned(items):\n    items.append(1)\n\n"
                "def sneaky(items):\n    items.append(1)\n"
            )
        }
        violations = run_dsl_rule(
            RULE,
            files, {"allow": ["pkg.mod:sanctioned"]}
        )
        assert len(violations) == 1
        assert "'pkg.mod:sneaky'" in violations[0].message

    def test_extra_mutators_extends_default_set(self, run_dsl_rule):
        files = {"pkg/mod.py": "def f(q):\n    q.enqueue(1)\n"}
        # Not a default collection mutator: clean by default.
        assert run_dsl_rule(RULE, files) == []
        violations = run_dsl_rule(
            RULE,
            files, {"extra-mutators": ["enqueue"]}
        )
        assert len(violations) == 1
        assert "parameter 'q' mutated via enqueue()" in violations[0].message

    def test_extra_mutators_keeps_defaults(self, run_dsl_rule):
        files = {
            "pkg/mod.py": "def f(q):\n    q.enqueue(1)\n    q.append(2)\n"
        }
        violations = run_dsl_rule(
            RULE,
            files, {"extra-mutators": ["enqueue"]}
        )
        assert {v.line for v in violations} == {2, 3}

    # ── registration / opt-in ───────────────────────────────────────────────

    def test_resolvable_by_id_in_the_rule_table(self):
        assert RULE in RULES

    def test_not_in_default_rules(self):
        # Available but not gated on pypeeker: the binder/planners deliberately
        # thread a mutable accumulator through visitors. See architecture.md.
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        assert RULE not in data["tool"]["pypeeker"]["rules"]
