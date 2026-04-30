"""Tests for the heuristic PurityChecker."""

from __future__ import annotations

from pypeeker.analysis import (
    EvidenceKind,
    PurityChecker,
    PurityVerdict,
)


def _evidence_kinds(result) -> list[EvidenceKind]:
    return [e.kind for e in result.evidence]


class TestPureFunctions:
    def test_function_only_reading_params_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def add(a, b):\n    return a + b\n"
        })
        result = PurityChecker(store).check("mod.py:add")
        assert result.verdict == PurityVerdict.PROBABLY_PURE
        assert result.evidence == []

    def test_local_assignment_is_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(x):\n    y = x + 1\n    z = y * 2\n    return z\n"
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.PROBABLY_PURE

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
        result = PurityChecker(store).check("mod.py:make_list")
        assert result.verdict == PurityVerdict.PROBABLY_PURE


class TestImpureBuiltinCalls:
    def test_print_call_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def shout(x):\n    print(x)\n"
        })
        result = PurityChecker(store).check("mod.py:shout")
        assert result.verdict == PurityVerdict.IMPURE
        assert EvidenceKind.CALLS_IMPURE_BUILTIN in _evidence_kinds(result)

    def test_open_call_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def read_file(p):\n    return open(p)\n"
        })
        result = PurityChecker(store).check("mod.py:read_file")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_BUILTIN and e.target == "open"
            for e in result.evidence
        )

    def test_input_call_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def ask():\n    return input('?')\n"
        })
        result = PurityChecker(store).check("mod.py:ask")
        assert result.verdict == PurityVerdict.IMPURE


class TestImpureStdlibCalls:
    def test_os_system_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "import os\ndef run(cmd):\n    os.system(cmd)\n"
        })
        result = PurityChecker(store).check("mod.py:run")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_STDLIB and e.detail == "system"
            for e in result.evidence
        )

    def test_time_time_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "import time\ndef now():\n    return time.time()\n"
        })
        result = PurityChecker(store).check("mod.py:now")
        assert result.verdict == PurityVerdict.IMPURE

    def test_random_random_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "import random\ndef roll():\n    return random.random()\n"
        })
        result = PurityChecker(store).check("mod.py:roll")
        assert result.verdict == PurityVerdict.IMPURE


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
        result = PurityChecker(store).check("mod.py:outer.inner")
        assert result.verdict == PurityVerdict.PROBABLY_PURE


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
        # Receiver is a parameter, not a local var, so it is not suppressed.
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_STDLIB and e.detail == "append"
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
        result = PurityChecker(store).check("mod.py:Calc.add")
        assert result.verdict == PurityVerdict.PROBABLY_PURE


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
        impure_calls = [
            e for e in result.evidence
            if e.kind == EvidenceKind.CALLS_IMPURE_BUILTIN
        ]
        assert len(impure_calls) == 1
        # Pypeeker uses 0-indexed lines; print(a) is the third source line.
        assert impure_calls[0].line == 2
        assert impure_calls[0].target == "print"
