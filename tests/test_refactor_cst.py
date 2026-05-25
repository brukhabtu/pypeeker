"""Tests for refactor/cst.py CST utilities."""

from __future__ import annotations

from pypeeker.models.transaction import EditOp
from pypeeker.refactor import cst


def test_expression_at_and_enclosing_statement():
    src = b"x = 1\nresult = compute(a, b) + 2\n"
    root = cst.parse(src)
    node = cst.expression_at(root, 1, 9)  # inside 'compute'
    assert cst.node_text(node, src) == "compute"
    stmt = cst.enclosing_statement(node)
    assert stmt.type == "expression_statement"
    assert cst.node_text(stmt, src) == "result = compute(a, b) + 2"


def test_node_spanning_call():
    src = b"y = foo(bar) + 1\n"
    root = cst.parse(src)
    call = cst.node_spanning(root, (0, 4), (0, 11))  # foo(bar)
    assert cst.node_text(call, src) == "foo(bar)"


def test_indent_and_line_start():
    src = b"def f():\n    value = a + b\n"
    root = cst.parse(src)
    node = cst.expression_at(root, 1, 12)  # 'a' in 'a + b'
    stmt = cst.enclosing_statement(node)
    assert cst.indent_of(stmt, src) == "    "
    assert cst.line_start_byte(stmt) == src.index(b"    value")


def test_replace_edit_byte_span():
    src = b"y = foo(bar)\n"
    root = cst.parse(src)
    call = cst.expression_at(root, 0, 4)  # foo call
    # widen to the whole call expression
    call = cst.enclosing_statement(call)  # expression_statement 'y = foo(bar)'
    edit = cst.replace_edit("m.py", call, "REPLACED", "hash", src)
    assert edit.op == EditOp.REPLACE
    assert src[edit.start:edit.end].decode() == "y = foo(bar)"
    assert edit.old == "y = foo(bar)" and edit.new == "REPLACED"


def test_insert_edit_zero_width():
    edit = cst.insert_edit("m.py", 0, "header = 1\n", "hash")
    assert edit.op == EditOp.INSERT
    assert edit.start == edit.end == 0
    assert edit.old == "" and edit.new == "header = 1\n"
