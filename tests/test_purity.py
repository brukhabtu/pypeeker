"""Tests for the heuristic PurityChecker."""

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    EvidenceKind,
    PurityChecker,
    PurityVerdict,
)
from pypeeker.models.capabilities import Confidence


def _evidence_kinds(result) -> list[EvidenceKind]:
    return [e.kind for e in result.evidence]


def _assert_pure(result) -> None:
    """Assert a verdict is PROBABLY_PURE with no evidence and HEURISTIC confidence."""
    assert result.verdict == PurityVerdict.PROBABLY_PURE, (
        f"expected PROBABLY_PURE, got {result.verdict.value}; "
        f"evidence: {result.evidence}"
    )
    assert result.evidence == [], f"expected no evidence; got {result.evidence}"
    assert result.confidence == Confidence.HEURISTIC


class TestPureFunctions:
    def test_function_only_reading_params_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def add(a, b):\n    return a + b\n"
        })
        _assert_pure(PurityChecker(store).check("mod.py:add"))

    def test_local_assignment_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(x):\n    y = x + 1\n    z = y * 2\n    return z\n"
        })
        _assert_pure(PurityChecker(store).check("mod.py:f"))

    def test_local_list_mutation_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def make_list():\n"
                "    items = []\n"
                "    items.append(1)\n"
                "    items.append(2)\n"
                "    return items\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:make_list"))


@pytest.mark.parametrize(
    "src, fn, expected_target",
    [
        ("def shout(x):\n    print(x)\n", "shout", "print"),
        ("def read_file(p):\n    return open(p)\n", "read_file", "open"),
        ("def ask():\n    return input('?')\n", "ask", "input"),
        ("def run():\n    return eval('1+1')\n", "run", "eval"),
        ("def run():\n    return exec('x = 1')\n", "run", "exec"),
    ],
)
def test_impure_builtin_call_is_flagged(indexed_project, src, fn, expected_target):
    _, store = indexed_project({"mod.py": src})
    result = PurityChecker(store).check(f"mod.py:{fn}")
    assert result.verdict == PurityVerdict.IMPURE
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert ev.kind == EvidenceKind.CALLS_IMPURE_BUILTIN
    assert ev.target == expected_target
    assert result.confidence == Confidence.HEURISTIC


@pytest.mark.parametrize(
    "import_line, call_expr, fn_body, expected_target",
    [
        ("import os", "os.system(cmd)", "    os.system(cmd)", "os.system"),
        ("import time", "time.time()", "    return time.time()", "time.time"),
        ("import random", "random.random()", "    return random.random()", "random.random"),
        ("import os", "os.unlink(p)", "    os.unlink(p)", "os.unlink"),
        ("import shutil", "shutil.rmtree(p)", "    shutil.rmtree(p)", "shutil.rmtree"),
    ],
)
def test_impure_module_call_is_flagged(
    indexed_project, import_line, call_expr, fn_body, expected_target
):
    src = f"{import_line}\ndef f(p, cmd=None):\n{fn_body}\n"
    _, store = indexed_project({"mod.py": src})
    result = PurityChecker(store).check("mod.py:f")
    assert result.verdict == PurityVerdict.IMPURE
    assert any(
        e.kind == EvidenceKind.CALLS_IMPURE_MODULE and e.target == expected_target
        for e in result.evidence
    )


class TestWritesToOuterScope:
    def test_global_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "counter = 0\n"
                "\n"
                "def bump():\n"
                "    global counter\n"
                "    counter += 1\n"
            )
        })
        result = PurityChecker(store).check("mod.py:bump")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.WRITES_OUTER_SCOPE
            and e.target == "mod.py:counter"
            for e in result.evidence
        )

    def test_nonlocal_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    x = 0\n"
                "    def inner():\n"
                "        nonlocal x\n"
                "        x += 1\n"
                "    return inner\n"
            )
        })
        result = PurityChecker(store).check("mod.py:outer.inner")
        assert result.verdict == PurityVerdict.IMPURE
        assert EvidenceKind.WRITES_OUTER_SCOPE in _evidence_kinds(result)

    def test_closure_read_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    x = 0\n"
                "    def inner():\n"
                "        return x + 1\n"
                "    return inner\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:outer.inner"))


class TestAttributeWrites:
    def test_self_attr_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "class Box:\n"
                "    def set_value(self, v):\n"
                "        self.value = v\n"
            )
        })
        result = PurityChecker(store).check("mod.py:Box.set_value")
        assert result.verdict == PurityVerdict.IMPURE
        assert EvidenceKind.ATTRIBUTE_WRITE in _evidence_kinds(result)


class TestParameterMutation:
    def test_arg_append_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def push(lst, item):\n    lst.append(item)\n"
        })
        result = PurityChecker(store).check("mod.py:push")
        assert result.verdict == PurityVerdict.IMPURE
        # Receiver is a parameter (caller-visible), so flagged.
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_METHOD and e.target == "append"
            for e in result.evidence
        )


class TestUnknownAndEdgeCases:
    def test_symbol_not_found(self, indexed_project):
        _, store = indexed_project({"mod.py": "def f(): pass\n"})
        result = PurityChecker(store).check("mod.py:does_not_exist")
        assert result.verdict == PurityVerdict.UNKNOWN
        assert EvidenceKind.NOT_FOUND in _evidence_kinds(result)

    def test_class_symbol_is_unknown(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "class Foo:\n    pass\n"
        })
        result = PurityChecker(store).check("mod.py:Foo")
        assert result.verdict == PurityVerdict.UNKNOWN
        assert EvidenceKind.NOT_A_FUNCTION in _evidence_kinds(result)

    def test_pure_method_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "class Calc:\n"
                "    def add(self, a, b):\n"
                "        return a + b\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:Calc.add"))


class TestDenylistOverMatchRegressions:
    """Regressions for names previously over-matched by IO_METHOD_NAMES.

    Each of these names is overloaded in non-IO domains and was producing
    false-positive evidence on real code (e.g. binder.bind != socket.bind).
    These tests pin down the chosen behavior.
    """

    def test_local_str_replace_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(s):\n    return s.replace('a', 'b')\n"
        })
        result = PurityChecker(store).check("mod.py:f")
        # Parameter-receiver replace is still flagged conservatively because
        # we can't tell str from Path. But on an unknown/dynamic chain it
        # should not fire spuriously.
        # Here we just assert no CALLS_IMPURE_METHOD for 'replace' on a
        # local string assigned from another local.
        _, store2 = indexed_project({
            "mod.py": "def f():\n    s = 'hello'\n    return s.replace('h', 'H')\n"
        })
        local_result = PurityChecker(store2).check("mod.py:f")
        assert local_result.verdict == PurityVerdict.PROBABLY_PURE

    def test_local_object_bind_is_pure(self, indexed_project):
        # `bind` was previously in IO_METHOD_NAMES, causing binder.bind() and
        # similar custom-object .bind() calls to flag falsely.
        _, store = indexed_project({
            "mod.py": (
                "def f(builder):\n"
                "    obj = builder.make()\n"
                "    obj.bind(some_target)\n"
                "    return obj\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        # Receiver is a local variable; bind is no longer a flagged method.
        # The test is mainly that no evidence kind CALLS_IMPURE_METHOD with
        # target='bind' appears.
        bind_evidence = [
            e for e in result.evidence
            if e.kind == EvidenceKind.CALLS_IMPURE_METHOD and e.target == "bind"
        ]
        assert bind_evidence == []

    def test_list_remove_on_local_is_pure(self, indexed_project):
        # list.remove(x) is collection mutation on a local — pure.
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    items = [1, 2, 3]\n"
                "    items.remove(2)\n"
                "    return items\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.PROBABLY_PURE

    def test_list_remove_on_parameter_is_impure(self, indexed_project):
        # list.remove(x) on a parameter mutates caller-visible state.
        _, store = indexed_project({
            "mod.py": "def f(items):\n    items.remove(2)\n"
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_METHOD and e.target == "remove"
            for e in result.evidence
        )


class TestEvidenceMetadata:
    def test_evidence_includes_line_numbers(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    a = 1\n"
                "    print(a)\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert len(result.evidence) == 1
        ev = result.evidence[0]
        assert ev.kind == EvidenceKind.CALLS_IMPURE_BUILTIN
        # Pypeeker uses 0-indexed lines; print(a) is the third source line.
        assert ev.line == 2
        assert ev.target == "print"
        assert result.confidence == Confidence.HEURISTIC

    def test_multiple_effects_produce_multiple_evidence(self, indexed_project):
        # One function with three impure operations across three categories:
        # an outer-scope write, an impure builtin, and an impure module call.
        _, store = indexed_project({
            "mod.py": (
                "import os\n"
                "counter = 0\n"
                "\n"
                "def busy():\n"
                "    global counter\n"
                "    counter += 1\n"
                "    print('a')\n"
                "    os.system('ls')\n"
            )
        })
        result = PurityChecker(store).check("mod.py:busy")
        assert result.verdict == PurityVerdict.IMPURE
        assert len(result.evidence) == 3
        kinds = {e.kind for e in result.evidence}
        assert kinds == {
            EvidenceKind.WRITES_OUTER_SCOPE,
            EvidenceKind.CALLS_IMPURE_BUILTIN,
            EvidenceKind.CALLS_IMPURE_MODULE,
        }
        # Lines are populated and in the function's body range (5..7).
        for ev in result.evidence:
            assert ev.line is not None
            assert 5 <= ev.line <= 7


class TestScopeIsolation:
    """Pin down that fact extractors and check_purity correctly scope to a
    single function, ignoring side effects in sibling and nested functions."""

    def test_sibling_impurity_does_not_leak(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def neighbor():\n"
                "    print('side effect')\n"
                "\n"
                "def target(a, b):\n"
                "    return a + b\n"
            )
        })
        # Sibling neighbor() has print() — must not appear in target's evidence.
        _assert_pure(PurityChecker(store).check("mod.py:target"))

    def test_outer_function_impurity_does_not_leak_into_inner(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    print('side effect')\n"
                "    def inner(a, b):\n"
                "        return a + b\n"
                "    return inner\n"
            )
        })
        # Inner is pure even though outer has print().
        _assert_pure(PurityChecker(store).check("mod.py:outer.inner"))

    def test_inner_function_impurity_does_not_leak_into_outer(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    def inner():\n"
                "        print('side effect')\n"
                "    return inner\n"
            )
        })
        # Outer just defines and returns inner — no impurity in outer's body.
        result = PurityChecker(store).check("mod.py:outer")
        assert result.verdict == PurityVerdict.PROBABLY_PURE
        # Note: with TASK-15 transitive analysis enabled, this would flip;
        # but the local check stays pure.

    def test_check_purity_pure_function_with_impure_neighbors(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "import os\n"
                "def writer():\n"
                "    print('a')\n"
                "    os.system('ls')\n"
                "\n"
                "def deleter(p):\n"
                "    p.unlink()\n"
                "\n"
                "def adder(a, b):\n"
                "    return a + b\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:adder"))


class TestRedsByLineScope:
    def test_reads_by_line_only_includes_function_under_analysis(
        self, analysis_context
    ):
        ctx = analysis_context(
            "x = 0\n"
            "y = 0\n"
            "\n"
            "def neighbor():\n"
            "    z = x\n"  # line 4
            "    return z\n"
            "\n"
            "def target():\n"
            "    a = y\n"  # line 8 — only this read should be in reads_by_line
            "    return a\n",
            "mod.py:target",
        )
        # All recorded read lines must be within target's body (>= 7).
        for line in ctx.reads_by_line.keys():
            assert line >= 7, (
                f"reads_by_line leaked a line ({line}) from outside target's scope"
            )


class TestTrickyConstructs:
    """Pin down behavior on Python constructs that are easy to get wrong."""

    def test_empty_function_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f():\n    pass\n"
        })
        _assert_pure(PurityChecker(store).check("mod.py:f"))

    def test_class_method_pass_body_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "class C:\n    def m(self):\n        pass\n"
        })
        _assert_pure(PurityChecker(store).check("mod.py:C.m"))

    def test_function_calling_project_internal_function_is_locally_pure(
        self, indexed_project
    ):
        # Documented baseline: without transitive analysis, calling another
        # project function (resolved CALL ref) is treated as pure. The
        # transitive check (TASK-15) handles propagation separately.
        _, store = indexed_project({
            "mod.py": (
                "def helper():\n"
                "    return 1\n\n"
                "def caller():\n"
                "    return helper()\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:caller"))

    def test_class_init_with_self_attr_is_impure(self, indexed_project):
        # Single self.x = y produces one ATTRIBUTE_WRITE evidence and the
        # function is flagged IMPURE. (Pypeeker's binder currently only
        # emits the ref for the first sequential self.x = y in a function;
        # multi-attr coverage is a binder-side follow-up, not a check-side
        # concern. Documenting current behavior so a future binder fix has
        # a regression target — see the binder TODO comment.)
        _, store = indexed_project({
            "mod.py": (
                "class C:\n"
                "    def __init__(self, a):\n"
                "        self.a = a\n"
            )
        })
        result = PurityChecker(store).check("mod.py:C.__init__")
        assert result.verdict == PurityVerdict.IMPURE
        attr_writes = [
            e for e in result.evidence if e.kind == EvidenceKind.ATTRIBUTE_WRITE
        ]
        assert len(attr_writes) == 1
        assert attr_writes[0].target == "<unresolved>.a"

    def test_decorated_function_resolves_normally(self, indexed_project):
        # A decorator should not break symbol resolution for purity.
        _, store = indexed_project({
            "mod.py": (
                "def deco(f):\n"
                "    return f\n\n"
                "@deco\n"
                "def f(a, b):\n"
                "    return a + b\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:f"))

    def test_generator_function_baseline(self, indexed_project):
        # Generators yield rather than return. Pypeeker has no dedicated
        # generator-detection rule today, so a generator with no other
        # impurity is currently PROBABLY_PURE. Documenting the baseline
        # so a future rule has a regression target.
        _, store = indexed_project({
            "mod.py": (
                "def gen():\n"
                "    yield 1\n"
                "    yield 2\n"
            )
        })
        result = PurityChecker(store).check("mod.py:gen")
        assert result.verdict == PurityVerdict.PROBABLY_PURE

    def test_lambda_body_does_not_leak_into_outer(self, indexed_project):
        # f = lambda x: print(x) — the lambda body runs only when f() is
        # called. The enclosing function's analysis must not include the
        # lambda's print() as evidence. (Same scope-isolation rule as
        # nested def.)
        _, store = indexed_project({
            "mod.py": (
                "def outer():\n"
                "    f = lambda x: print(x)\n"
                "    return f\n"
            )
        })
        _assert_pure(PurityChecker(store).check("mod.py:outer"))

    def test_comprehension_with_print_is_impure(self, indexed_project):
        # Comprehensions execute inline, so a side effect inside a
        # comprehension IS a side effect of the enclosing function.
        _, store = indexed_project({
            "mod.py": (
                "def f(xs):\n"
                "    return [print(x) for x in xs]\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_BUILTIN and e.target == "print"
            for e in result.evidence
        )
