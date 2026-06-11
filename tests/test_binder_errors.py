"""Tests for FileIndex.errors: syntax errors recorded by the binder.

The binder collects tree-sitter ERROR / missing nodes into
``FileIndex.errors`` so a partially bound index is visibly partial.
"""


def test_clean_source_has_no_errors(bind_source):
    index = bind_source("def foo():\n    return 1\n\nfoo()\n")
    assert index.errors == []


def test_malformed_source_records_error(bind_source):
    # "def (:" after a valid function is unparseable
    index = bind_source("def foo():\n    return 1\n\ndef (:\n")
    assert index.errors
    assert any("syntax error" in e or "missing" in e for e in index.errors)


def test_error_entries_include_position(bind_source):
    index = bind_source("x = 1\n???\n")
    assert index.errors
    assert any("line 2" in e for e in index.errors)


def test_valid_symbols_still_bind_despite_errors(bind_source):
    # Valid definitions around the malformed stretch must still be indexed.
    source = "def good():\n    return 1\n\n???\n\nclass AlsoGood:\n    pass\n"
    index = bind_source(source)

    assert index.errors  # the malformed stretch is recorded
    names = {s.name for s in index.symbols}
    assert "good" in names
    assert "AlsoGood" in names


def test_missing_token_recorded(bind_source):
    # Unclosed paren: tree-sitter inserts a missing ")" token.
    index = bind_source("foo(1, 2\n")
    assert index.errors
    assert any("missing" in e or "syntax error" in e for e in index.errors)


def test_errors_survive_serialization_round_trip(bind_source):
    from pypeeker.models.index import FileIndex
    from pypeeker.models.serialize import from_json, to_json

    index = bind_source("def foo(:\n    pass\n")
    assert index.errors
    restored = from_json(FileIndex, to_json(index))
    assert restored.errors == index.errors
