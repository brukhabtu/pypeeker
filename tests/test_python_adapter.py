"""Tests for the Python language adapter."""

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.models.symbols import Visibility


def test_parse_simple_module():
    adapter = PythonAdapter()
    tree = adapter.parse(b"x = 1\n")
    assert tree.root_node.type == "module"


def test_parse_function():
    adapter = PythonAdapter()
    tree = adapter.parse(b"def foo():\n    pass\n")
    func_node = tree.root_node.children[0]
    assert func_node.type == "function_definition"


def test_parse_class():
    adapter = PythonAdapter()
    tree = adapter.parse(b"class Foo:\n    pass\n")
    cls_node = tree.root_node.children[0]
    assert cls_node.type == "class_definition"


def test_visibility_public():
    adapter = PythonAdapter()
    assert adapter.get_visibility("my_func") == Visibility.PUBLIC


def test_visibility_protected():
    adapter = PythonAdapter()
    assert adapter.get_visibility("_private") == Visibility.PROTECTED


def test_visibility_private():
    adapter = PythonAdapter()
    assert adapter.get_visibility("__mangled") == Visibility.PRIVATE


def test_visibility_dunder():
    adapter = PythonAdapter()
    assert adapter.get_visibility("__init__") == Visibility.DUNDER


def test_language_name():
    adapter = PythonAdapter()
    assert adapter.language_name == "python"
