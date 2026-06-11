"""Tests for data models."""

from pypeeker.models.capabilities import _Capability as Capability, Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.location import Location, Position, Span
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind, TypeAnnotation, Visibility
from pypeeker.models.serialize import from_json, to_json


def test_position_roundtrip():
    pos = Position(line=10, column=5)
    data = to_json(pos)
    restored = from_json(Position, data)
    assert restored == pos


def test_span_roundtrip():
    span = Span(start=Position(line=1, column=0), end=Position(line=5, column=10))
    data = to_json(span)
    restored = from_json(Span, data)
    assert restored == span


def test_location_roundtrip():
    loc = Location(
        file_path="src/main.py",
        span=Span(start=Position(line=1, column=0), end=Position(line=1, column=10)),
    )
    data = to_json(loc)
    restored = from_json(Location, data)
    assert restored == loc


def test_symbol_roundtrip():
    sym = Symbol(
        symbol_id="src.main:MyClass.method",
        name="method",
        kind=SymbolKind.METHOD,
        location=Location(
            file_path="src/main.py",
            span=Span(
                start=Position(line=5, column=4), end=Position(line=5, column=10)
            ),
        ),
        visibility=Visibility.PUBLIC,
        visibility_confidence=Confidence.HEURISTIC,
        type_annotation=TypeAnnotation(raw="str", confidence=Confidence.DECLARED),
        decorators=["staticmethod"],
        docstring="A method.",
        parent_scope_id="src.main:MyClass",
    )
    data = to_json(sym)
    restored = from_json(Symbol, data)
    assert restored == sym
    assert restored.type_annotation is not None
    assert restored.type_annotation.raw == "str"


def test_scope_roundtrip():
    scope = Scope(
        scope_id="src.main:MyClass",
        name="MyClass",
        kind=ScopeKind.CLASS,
        file_path="src/main.py",
        span=Span(start=Position(line=1, column=0), end=Position(line=10, column=0)),
        parent_scope_id="src/main.py",
        child_scope_ids=["src.main:MyClass.method"],
        symbol_ids=["src.main:MyClass:x"],
    )
    data = to_json(scope)
    restored = from_json(Scope, data)
    assert restored == scope


def test_reference_roundtrip():
    ref = Reference(
        symbol_id="src.main:MyClass",
        kind=ReferenceKind.CALL,
        location=Location(
            file_path="src/main.py",
            span=Span(
                start=Position(line=15, column=4), end=Position(line=15, column=11)
            ),
        ),
        in_scope_id="src/main.py",
        resolved=True,
    )
    data = to_json(ref)
    restored = from_json(Reference, data)
    assert restored == ref


def test_file_index_roundtrip():
    idx = FileIndex(
        file_path="src/main.py",
        file_hash="abc123",
        language="python",
        symbols=[],
        scopes=[],
        references=[],
        errors=["some warning"],
    )
    data = to_json(idx)
    restored = from_json(FileIndex, data)
    assert restored == idx
    assert restored.errors == ["some warning"]


def test_file_index_forward_compat():
    """Missing keys fall back to defaults; unknown keys are ignored."""
    from pypeeker.models.serialize import from_dict

    # Old index without "errors", plus a key from a hypothetical newer version
    data = {
        "file_path": "src/main.py",
        "file_hash": "abc123",
        "language": "python",
        "symbols": [],
        "scopes": [],
        "references": [],
        "future_field": "ignored",
    }
    restored = from_dict(FileIndex, data)
    assert restored.errors == []
    assert restored.file_path == "src/main.py"


def test_transaction_header_status_forward_compat():
    """Header without "status" defaults to PENDING; unknown keys ignored."""
    from pypeeker.models.serialize import from_dict
    from pypeeker.models.transaction import TransactionHeader, TransactionStatus

    data = {
        "tx_id": "tx1",
        "symbol_id": "test:foo",
        "old_name": "foo",
        "new_name": "bar",
        "created_at": "2025-01-01T00:00:00+00:00",
        "future_field": "ignored",
    }
    restored = from_dict(TransactionHeader, data)
    assert restored.status == TransactionStatus.PENDING

    data["status"] = "applied"
    assert from_dict(TransactionHeader, data).status == TransactionStatus.APPLIED


def test_enum_serialization():
    assert Capability.VISIBILITY.value == "visibility"
    assert Confidence.DECLARED.value == "declared"
    assert SymbolKind.FUNCTION.value == "function"
    assert ScopeKind.MODULE.value == "module"
    assert ReferenceKind.CALL.value == "call"
    assert Visibility.PRIVATE.value == "private"
