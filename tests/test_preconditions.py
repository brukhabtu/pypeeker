"""Tests for first-class planner preconditions.

Covers, per acceptance criteria of TASK-85:
- each precondition passing and failing in isolation,
- enumerability of each planner's precondition set,
- identity between precondition reasons and the planner error messages.
"""

from __future__ import annotations

import pytest

from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor import cst
from pypeeker.refactor.extract import (
    ExtractMethodError,
    ExtractMethodPlanner,
    ExtractVariablePlanner,
)
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.refactor.preconditions import (
    AffectedFilesFresh,
    AssignmentLocatable,
    ExpressionFound,
    FileExists,
    FileFresh,
    InsideStatement,
    LoadedIndexFresh,
    LocalVariableResolves,
    MultiUseValuePure,
    NewNameDiffers,
    NoControlFlowEscape,
    NoScopeNameConflict,
    NotReassigned,
    Precondition,
    PreconditionResult,
    RangeInsideFunction,
    RenameFlagsCompatible,
    SymbolResolvesUniquely,
    TopLevelFunctionOnly,
    ValidIdentifier,
    evaluate_in_order,
)
from pypeeker.storage import TransactionStore


# ---------------------------------------------------------------------------
# Framework
# ---------------------------------------------------------------------------


class TestEvaluateInOrder:
    def test_all_pass(self):
        pres = [ValidIdentifier("a"), ValidIdentifier("b")]
        evaluated, failure = evaluate_in_order(pres)
        assert evaluated == pres
        assert failure is None

    def test_stops_at_first_failure(self):
        pres = [ValidIdentifier("ok"), ValidIdentifier("1bad"), ValidIdentifier("2bad")]
        evaluated, failure = evaluate_in_order(pres)
        assert evaluated == pres[:2]
        assert failure == PreconditionResult(
            ok=False, reason="Invalid Python identifier: 1bad"
        )

    def test_does_not_advance_generator_past_failure(self):
        advanced = []

        def gen():
            advanced.append("first")
            yield ValidIdentifier("1bad")
            advanced.append("second")
            yield ValidIdentifier("ok")

        evaluated, failure = evaluate_in_order(gen())
        assert failure is not None
        assert len(evaluated) == 1
        assert advanced == ["first"]


# ---------------------------------------------------------------------------
# Shared preconditions
# ---------------------------------------------------------------------------


class TestValidIdentifier:
    def test_pass(self):
        assert ValidIdentifier("new_name").evaluate().ok

    def test_fail(self):
        result = ValidIdentifier("1bad").evaluate()
        assert not result.ok
        assert result.reason == "Invalid Python identifier: 1bad"


class TestFileExists:
    def test_pass(self, indexed_project):
        _, store = indexed_project({"m.py": "x = 1\n"})
        assert FileExists(store, "m.py").evaluate().ok

    def test_fail(self, indexed_project):
        _, store = indexed_project({"m.py": "x = 1\n"})
        result = FileExists(store, "nope.py").evaluate()
        assert not result.ok
        assert result.reason == "File not found: nope.py"


class TestFileFresh:
    def test_pass(self, indexed_project):
        _, store = indexed_project({"m.py": "x = 1\n"})
        assert FileFresh(store, "m.py").evaluate().ok

    def test_fail_after_modification(self, indexed_project):
        project, store = indexed_project({"m.py": "x = 1\n"})
        (project / "m.py").write_text("x = 1\n# changed\n")
        result = FileFresh(store, "m.py").evaluate()
        assert not result.ok
        assert result.reason == "File is stale or not indexed: m.py"


# ---------------------------------------------------------------------------
# Rename preconditions
# ---------------------------------------------------------------------------


class TestRenameFlagsCompatible:
    def test_pass(self):
        assert RenameFlagsCompatible(True, False).evaluate().ok
        assert RenameFlagsCompatible(False, True).evaluate().ok

    def test_fail(self):
        result = RenameFlagsCompatible(True, True).evaluate()
        assert not result.ok
        assert result.reason == (
            "--include-exports and --keep-export are mutually exclusive: "
            "one changes the public export name, the other preserves it."
        )


class TestSymbolResolvesUniquely:
    def test_pass_caches_symbol(self, indexed_project):
        _, store = indexed_project({"m.py": "def f(): pass\n"})
        pre = SymbolResolvesUniquely(SemanticQueryEngine(store), "m:f")
        assert pre.evaluate().ok
        assert pre.symbol is not None
        assert pre.symbol.name == "f"

    def test_fail_not_found(self, indexed_project):
        _, store = indexed_project({"m.py": "def f(): pass\n"})
        result = SymbolResolvesUniquely(SemanticQueryEngine(store), "m:nope").evaluate()
        assert not result.ok
        assert result.reason == "Symbol not found: m:nope"

    def test_fail_ambiguous(self, indexed_project):
        _, store = indexed_project({
            "a.py": "def foo(): pass\n",
            "b.py": "def foo(): pass\n",
        })
        result = SymbolResolvesUniquely(SemanticQueryEngine(store), "foo").evaluate()
        assert not result.ok
        assert result.reason.startswith("Ambiguous symbol 'foo', matched 2:")
        assert result.reason.endswith("Use the full symbol ID to disambiguate.")


class TestNewNameDiffers:
    def test_pass(self):
        assert NewNameDiffers("old", "new").evaluate().ok

    def test_fail(self):
        result = NewNameDiffers("foo", "foo").evaluate()
        assert not result.ok
        assert result.reason == "New name is same as old name: foo"


class TestNoScopeNameConflict:
    def test_pass(self, indexed_project):
        _, store = indexed_project({"m.py": "def foo(): pass\ndef bar(): pass\n"})
        symbol = SemanticQueryEngine(store).find_symbol("m:foo")[0]
        assert NoScopeNameConflict(store, symbol, "baz").evaluate().ok

    def test_fail(self, indexed_project):
        _, store = indexed_project({"m.py": "def foo(): pass\ndef bar(): pass\n"})
        engine = SemanticQueryEngine(store)
        symbol = engine.find_symbol("m:foo")[0]
        other = engine.find_symbol("m:bar")[0]
        result = NoScopeNameConflict(store, symbol, "bar").evaluate()
        assert not result.ok
        assert result.reason == (
            f"Name conflict: 'bar' already exists in scope "
            f"'{symbol.parent_scope_id}' as {other.symbol_id}"
        )


class TestAffectedFilesFresh:
    def test_pass(self, indexed_project):
        _, store = indexed_project({"m.py": "x = 1\n"})
        assert AffectedFilesFresh(store, {"m.py"}).evaluate().ok

    def test_fail(self, indexed_project):
        project, store = indexed_project({"m.py": "x = 1\n"})
        (project / "m.py").write_text("x = 1\n# changed\n")
        result = AffectedFilesFresh(store, {"m.py"}).evaluate()
        assert not result.ok
        assert result.reason == (
            "File 'm.py' is stale or not indexed. Run 'pypeeker index' first."
        )


# ---------------------------------------------------------------------------
# Extract-variable preconditions
# ---------------------------------------------------------------------------

_EXTRACT_SRC = "def f():\n    return foo(bar) + 2\n"


class TestExpressionFound:
    def test_pass_caches_node(self):
        source = _EXTRACT_SRC.encode()
        root = cst.parse(source)
        pre = ExpressionFound(root, (1, 11), (1, 19), "m.py")
        assert pre.evaluate().ok
        assert pre.node is not None
        assert cst.node_text(pre.node, source) == "foo(bar)"

    def test_fail_non_expression(self):
        root = cst.parse(_EXTRACT_SRC.encode())
        result = ExpressionFound(root, (0, 0), (1, 5), "m.py").evaluate()
        assert not result.ok
        assert result.reason == "No expression found at m.py:0:0"


class TestInsideStatement:
    def test_pass_caches_statement(self):
        source = _EXTRACT_SRC.encode()
        root = cst.parse(source)
        node = cst.node_spanning(root, (1, 11), (1, 19))
        pre = InsideStatement(node)
        assert pre.evaluate().ok
        assert pre.statement is not None

    def test_fail_for_module_node(self):
        root = cst.parse(_EXTRACT_SRC.encode())
        result = InsideStatement(root).evaluate()
        assert not result.ok
        assert result.reason == "Selection is not inside a statement"


# ---------------------------------------------------------------------------
# Extract-method preconditions
# ---------------------------------------------------------------------------

_METHOD_SRC = "def f(a):\n    x = a + 1\n    y = x * 2\n    return y\n"


class TestRangeInsideFunction:
    def test_pass_caches_dataflow(self, indexed_project):
        _, store = indexed_project({"m.py": _METHOD_SRC})
        pre = RangeInsideFunction(store, "m.py", 1, 2)
        assert pre.evaluate().ok
        assert pre.dataflow is not None

    def test_fail_module_level(self, indexed_project):
        _, store = indexed_project({"m.py": "x = 1\ny = 2\n"})
        result = RangeInsideFunction(store, "m.py", 0, 1).evaluate()
        assert not result.ok
        assert result.reason == "Range is not inside a function"


class TestNoControlFlowEscape:
    def test_pass(self, indexed_project):
        _, store = indexed_project({"m.py": _METHOD_SRC})
        in_function = RangeInsideFunction(store, "m.py", 1, 2)
        assert in_function.evaluate().ok
        assert NoControlFlowEscape(in_function.dataflow).evaluate().ok

    def test_fail_on_return(self, indexed_project):
        _, store = indexed_project({"m.py": _METHOD_SRC})
        in_function = RangeInsideFunction(store, "m.py", 1, 3)
        assert in_function.evaluate().ok
        result = NoControlFlowEscape(in_function.dataflow).evaluate()
        assert not result.ok
        assert result.reason == (
            "Range contains return/break/continue; cannot extract safely"
        )


class TestTopLevelFunctionOnly:
    def test_pass_caches_scope(self, indexed_project):
        _, store = indexed_project({"m.py": _METHOD_SRC})
        pre = TopLevelFunctionOnly(store, "m.py", 1, 2)
        assert pre.evaluate().ok
        assert pre.func_scope is not None

    def test_fail_for_method(self, indexed_project):
        _, store = indexed_project({
            "m.py": "class C:\n    def m(self):\n        x = 1\n        y = x\n"
        })
        result = TopLevelFunctionOnly(store, "m.py", 2, 3).evaluate()
        assert not result.ok
        assert result.reason == "extract-method v1 supports only top-level functions"


# ---------------------------------------------------------------------------
# Inline-variable preconditions
# ---------------------------------------------------------------------------

_INLINE_SRC = "def f(a):\n    x = a + 1\n    return x\n"


class TestLocalVariableResolves:
    def test_pass_caches_symbol(self, indexed_project):
        _, store = indexed_project({"m.py": _INLINE_SRC})
        pre = LocalVariableResolves(SemanticQueryEngine(store), store, "m:f:x")
        assert pre.evaluate().ok
        assert pre.symbol is not None
        assert pre.symbol.name == "x"

    def test_fail_not_found(self, indexed_project):
        _, store = indexed_project({"m.py": _INLINE_SRC})
        pre = LocalVariableResolves(SemanticQueryEngine(store), store, "m:f:zzz")
        result = pre.evaluate()
        assert not result.ok
        assert result.reason == "Symbol not found: m:f:zzz"

    def test_fail_ambiguous(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f():\n    x = 1\n    return x\ndef g():\n    x = 2\n    return x\n"
        })
        pre = LocalVariableResolves(SemanticQueryEngine(store), store, "x")
        result = pre.evaluate()
        assert not result.ok
        assert result.reason == "Ambiguous symbol 'x'; use the full id"

    def test_fail_not_a_variable(self, indexed_project):
        _, store = indexed_project({"m.py": _INLINE_SRC})
        pre = LocalVariableResolves(SemanticQueryEngine(store), store, "m:f")
        result = pre.evaluate()
        assert not result.ok
        assert result.reason == "inline-variable only applies to variables"

    def test_fail_not_function_local(self, indexed_project):
        _, store = indexed_project({"m.py": "x = 1\ny = x\n"})
        pre = LocalVariableResolves(SemanticQueryEngine(store), store, "m:x")
        result = pre.evaluate()
        assert not result.ok
        assert result.reason == (
            "inline-variable v1 supports only function-local variables"
        )


class TestLoadedIndexFresh:
    def test_pass_caches_index(self, indexed_project):
        _, store = indexed_project({"m.py": _INLINE_SRC})
        pre = LoadedIndexFresh(store, "m.py")
        assert pre.evaluate().ok
        assert pre.index is not None

    def test_fail_after_modification(self, indexed_project):
        project, store = indexed_project({"m.py": _INLINE_SRC})
        (project / "m.py").write_text(_INLINE_SRC + "# changed\n")
        result = LoadedIndexFresh(store, "m.py").evaluate()
        assert not result.ok
        assert result.reason == "File is stale or not indexed: m.py"


class TestNotReassigned:
    def test_pass(self, indexed_project):
        _, store = indexed_project({"m.py": _INLINE_SRC})
        symbol = SemanticQueryEngine(store).find_symbol("m:f:x")[0]
        index = store.load("m.py")
        assert NotReassigned(symbol, index).evaluate().ok

    def test_fail_reassigned(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(a):\n    x = 1\n    x = 2\n    return x\n"
        })
        index = store.load("m.py")
        symbol = next(s for s in index.symbols if s.name == "x")
        result = NotReassigned(symbol, index).evaluate()
        assert not result.ok
        assert result.reason == "Variable is reassigned; cannot inline"


class TestMultiUseValuePure:
    def test_pass_pure_multi_use(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(a):\n    x = a + 1\n    return x + x\n"
        })
        assert MultiUseValuePure(store, "m.py", 1, 2).evaluate().ok

    def test_pass_impure_single_use(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(items):\n    x = items.pop()\n    return x\n"
        })
        assert MultiUseValuePure(store, "m.py", 1, 1).evaluate().ok

    def test_fail_impure_multi_use(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(items):\n    x = items.pop()\n    return x + x\n"
        })
        result = MultiUseValuePure(store, "m.py", 1, 2).evaluate()
        assert not result.ok
        assert result.reason == (
            "Value has side effects and is used more than once; "
            "inlining would change behavior"
        )


class TestAssignmentLocatable:
    def test_pass_caches_rhs(self, indexed_project):
        project, store = indexed_project({"m.py": _INLINE_SRC})
        symbol = SemanticQueryEngine(store).find_symbol("m:f:x")[0]
        source = (project / "m.py").read_bytes()
        pre = AssignmentLocatable(cst.parse(source), symbol)
        assert pre.evaluate().ok
        assert pre.rhs is not None
        assert cst.node_text(pre.rhs, source) == "a + 1"

    def test_fail_not_simple_assignment(self, indexed_project):
        project, store = indexed_project({
            "m.py": "def f():\n    for x in range(3):\n        pass\n"
        })
        symbol = SemanticQueryEngine(store).find_symbol("m:f:x")[0]
        source = (project / "m.py").read_bytes()
        result = AssignmentLocatable(cst.parse(source), symbol).evaluate()
        assert not result.ok
        assert result.reason == "Variable is not a simple assignment"


# ---------------------------------------------------------------------------
# Enumerability of each planner's precondition set
# ---------------------------------------------------------------------------


def _names(preconditions: list[Precondition]) -> list[str]:
    return [p.name for p in preconditions]


class TestPlannerPreconditionSets:
    def test_rename_set(self, indexed_project):
        _, store = indexed_project({"test.py": "def greet():\n    pass\n\ngreet()\n"})
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        preconditions = planner.preconditions("test:greet", "hello")
        assert _names(preconditions) == [
            "rename-flags-compatible",
            "symbol-resolves-uniquely",
            "new-name-differs",
            "valid-identifier",
            "no-scope-name-conflict",
            "affected-files-fresh",
        ]
        assert all(p.evaluate().ok for p in preconditions)

    def test_rename_set_truncates_at_failure(self, indexed_project):
        _, store = indexed_project({"test.py": "def greet():\n    pass\n"})
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        preconditions = planner.preconditions("test:nope", "hello")
        assert _names(preconditions) == [
            "rename-flags-compatible",
            "symbol-resolves-uniquely",
        ]
        result = preconditions[-1].evaluate()
        assert not result.ok
        assert result.reason == "Symbol not found: test:nope"

    def test_extract_variable_set(self, indexed_project):
        _, store = indexed_project({"m.py": _EXTRACT_SRC})
        planner = ExtractVariablePlanner(store, TransactionStore(store.project_root))
        preconditions = planner.preconditions("m.py", (1, 11), (1, 19), "value")
        assert _names(preconditions) == [
            "valid-identifier",
            "file-exists",
            "file-fresh",
            "expression-found",
            "inside-statement",
        ]
        assert all(p.evaluate().ok for p in preconditions)

    def test_extract_method_set(self, indexed_project):
        _, store = indexed_project({"m.py": _METHOD_SRC})
        planner = ExtractMethodPlanner(store, TransactionStore(store.project_root))
        preconditions = planner.preconditions("m.py", 1, 2, "helper")
        assert _names(preconditions) == [
            "valid-identifier",
            "file-fresh",
            "range-inside-function",
            "no-control-flow-escape",
            "top-level-function-only",
        ]
        assert all(p.evaluate().ok for p in preconditions)

    def test_inline_set(self, indexed_project):
        _, store = indexed_project({"m.py": _INLINE_SRC})
        planner = InlineVariablePlanner(store, TransactionStore(store.project_root))
        preconditions = planner.preconditions("m:f:x")
        assert _names(preconditions) == [
            "local-variable-resolves",
            "loaded-index-fresh",
            "not-reassigned",
            "multi-use-value-pure",
            "assignment-locatable",
        ]
        assert all(p.evaluate().ok for p in preconditions)


# ---------------------------------------------------------------------------
# Message identity: planner errors equal precondition reasons exactly
# ---------------------------------------------------------------------------


class TestMessageIdentity:
    def test_rename_invalid_identifier(self, indexed_project):
        _, store = indexed_project({"test.py": "def foo(): pass\n"})
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        with pytest.raises(RenamePlanError) as exc:
            planner.plan("test:foo", "123invalid")
        assert str(exc.value) == ValidIdentifier("123invalid").evaluate().reason

    def test_rename_stale_file(self, indexed_project):
        project, store = indexed_project({"test.py": "def foo(): pass\n"})
        (project / "test.py").write_text("def foo(): pass\n# changed\n")
        planner = RenamePlanner(store, TransactionStore(store.project_root))
        with pytest.raises(RenamePlanError) as exc:
            planner.plan("test:foo", "bar")
        assert str(exc.value) == AffectedFilesFresh(store, {"test.py"}).evaluate().reason

    def test_extract_method_escape(self, indexed_project):
        _, store = indexed_project({"m.py": _METHOD_SRC})
        planner = ExtractMethodPlanner(store, TransactionStore(store.project_root))
        with pytest.raises(ExtractMethodError) as exc:
            planner.plan("m.py", 1, 3, "helper")
        in_function = RangeInsideFunction(store, "m.py", 1, 3)
        assert in_function.evaluate().ok
        assert (
            str(exc.value)
            == NoControlFlowEscape(in_function.dataflow).evaluate().reason
        )

    def test_inline_reassigned(self, indexed_project):
        _, store = indexed_project({
            "m.py": "def f(a):\n    x = 1\n    x = 2\n    return x\n"
        })
        planner = InlineVariablePlanner(store, TransactionStore(store.project_root))
        index = store.load("m.py")
        symbol = next(s for s in index.symbols if s.name == "x")
        with pytest.raises(InlineVariableError) as exc:
            planner.plan(symbol.symbol_id)
        assert str(exc.value) == NotReassigned(symbol, index).evaluate().reason
