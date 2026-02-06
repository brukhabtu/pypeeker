"""Tests for data models."""

from pypeeker.models.capabilities import Capability, Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.location import Location, Position, Span
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind, TypeAnnotation, Visibility


def test_position_roundtrip():
    pos = Position(line=10, column=5)
    data = pos.model_dump_json()
    restored = Position.model_validate_json(data)
    assert restored == pos


def test_span_roundtrip():
    span = Span(start=Position(line=1, column=0), end=Position(line=5, column=10))
    data = span.model_dump_json()
    restored = Span.model_validate_json(data)
    assert restored == span


def test_location_roundtrip():
    loc = Location(
        file_path="src/main.py",
        span=Span(start=Position(line=1, column=0), end=Position(line=1, column=10)),
    )
    data = loc.model_dump_json()
    restored = Location.model_validate_json(data)
    assert restored == loc


def test_symbol_roundtrip():
    sym = Symbol(
        symbol_id="src/main.py:MyClass.method",
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
        parent_scope_id="src/main.py:MyClass",
    )
    data = sym.model_dump_json()
    restored = Symbol.model_validate_json(data)
    assert restored == sym
    assert restored.type_annotation is not None
    assert restored.type_annotation.raw == "str"


def test_scope_roundtrip():
    scope = Scope(
        scope_id="src/main.py:MyClass",
        name="MyClass",
        kind=ScopeKind.CLASS,
        file_path="src/main.py",
        span=Span(start=Position(line=1, column=0), end=Position(line=10, column=0)),
        parent_scope_id="src/main.py",
        child_scope_ids=["src/main.py:MyClass.method"],
        symbol_ids=["src/main.py:MyClass:x"],
    )
    data = scope.model_dump_json()
    restored = Scope.model_validate_json(data)
    assert restored == scope


def test_reference_roundtrip():
    ref = Reference(
        symbol_id="src/main.py:MyClass",
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
    data = ref.model_dump_json()
    restored = Reference.model_validate_json(data)
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
    data = idx.model_dump_json()
    restored = FileIndex.model_validate_json(data)
    assert restored == idx
    assert restored.errors == ["some warning"]


def test_enum_serialization():
    assert Capability.VISIBILITY.value == "visibility"
    assert Confidence.DECLARED.value == "declared"
    assert SymbolKind.FUNCTION.value == "function"
    assert ScopeKind.MODULE.value == "module"
    assert ReferenceKind.CALL.value == "call"
    assert Visibility.PRIVATE.value == "private"
