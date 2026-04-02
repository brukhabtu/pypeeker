"""Tests for the Python language adapter."""

import pytest

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.models.capabilities import Capability, Confidence
from pypeeker.models.symbols import Visibility

pytestmark = pytest.mark.unit


def test_parse_simple_module():
    adapter = PythonAdapter()
    tree = adapter.parse(b"x = 1\n")
    assert tree.root_node.type == "module"


def test_parse_function():
    adapter = PythonAdapter()
    tree = adapter.parse(b"def foo():\n    pass\n")
    func_node = tree.root_node.children[0]
    assert func_node.type == "function_definition"
    assert adapter.is_scope_node(func_node)
    assert adapter.is_declaration_node(func_node)


def test_parse_class():
    adapter = PythonAdapter()
    tree = adapter.parse(b"class Foo:\n    pass\n")
    cls_node = tree.root_node.children[0]
    assert cls_node.type == "class_definition"
    assert adapter.is_scope_node(cls_node)
    assert adapter.is_declaration_node(cls_node)


def test_extract_name_function():
    adapter = PythonAdapter()
    tree = adapter.parse(b"def my_func():\n    pass\n")
    func_node = tree.root_node.children[0]
    assert adapter.extract_name(func_node) == "my_func"


def test_extract_name_class():
    adapter = PythonAdapter()
    tree = adapter.parse(b"class MyClass:\n    pass\n")
    cls_node = tree.root_node.children[0]
    assert adapter.extract_name(cls_node) == "MyClass"


def test_is_reference_node():
    adapter = PythonAdapter()
    tree = adapter.parse(b"x = y\n")
    # expression_statement wraps assignment
    expr_stmt = tree.root_node.children[0]
    assignment = expr_stmt.children[0]
    right = assignment.child_by_field_name("right")
    assert right.type == "identifier"
    assert adapter.is_reference_node(right)


def test_visibility_public():
    adapter = PythonAdapter()
    vis, conf = adapter.get_visibility("my_func")
    assert vis == Visibility.PUBLIC
    assert conf == Confidence.HEURISTIC


def test_visibility_protected():
    adapter = PythonAdapter()
    vis, conf = adapter.get_visibility("_private")
    assert vis == Visibility.PROTECTED
    assert conf == Confidence.HEURISTIC


def test_visibility_private():
    adapter = PythonAdapter()
    vis, conf = adapter.get_visibility("__mangled")
    assert vis == Visibility.PRIVATE
    assert conf == Confidence.HEURISTIC


def test_visibility_dunder():
    adapter = PythonAdapter()
    vis, conf = adapter.get_visibility("__init__")
    assert vis == Visibility.DUNDER
    assert conf == Confidence.HEURISTIC


def test_capabilities():
    adapter = PythonAdapter()
    caps = adapter.capabilities
    assert Capability.VISIBILITY in caps
    assert caps[Capability.VISIBILITY] == Confidence.HEURISTIC
    assert Capability.STATIC_TYPES in caps
    assert caps[Capability.STATIC_TYPES] == Confidence.DECLARED


def test_get_type_annotation():
    adapter = PythonAdapter()
    tree = adapter.parse(b"x: int = 5\n")
    # expression_statement wraps assignment
    expr_stmt = tree.root_node.children[0]
    assignment = expr_stmt.children[0]
    raw, conf = adapter.get_type_annotation(assignment)
    assert raw == "int"
    assert conf == Confidence.DECLARED


def test_get_type_annotation_missing():
    adapter = PythonAdapter()
    tree = adapter.parse(b"x = 5\n")
    expr_stmt = tree.root_node.children[0]
    assignment = expr_stmt.children[0]
    raw, conf = adapter.get_type_annotation(assignment)
    assert raw is None
    assert conf == Confidence.UNKNOWN


def test_is_scope_node_comprehension():
    adapter = PythonAdapter()
    tree = adapter.parse(b"[x for x in range(10)]\n")
    expr = tree.root_node.children[0]
    comp = expr.children[0] if expr.type == "expression_statement" else expr
    assert adapter.is_scope_node(comp)


def test_language_name():
    adapter = PythonAdapter()
    assert adapter.language_name == "python"
