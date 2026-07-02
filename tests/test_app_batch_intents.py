"""Direct unit tests for :mod:`pypeeker.app.batch_intents` (no CliRunner).

``build_batch_intents`` turns a ``plan-batch`` intents file's parsed JSON
into intent objects; extracting it out of ``cli.py`` makes its validation
and expansion logic testable without spawning the CLI. Covers malformed
input (one case per validation error), dep resolution (including a "fix"
entry expanding into several intents), and the "fix" -> FixIntent expansion
against a real check rule.
"""

from __future__ import annotations

import pytest

from pypeeker.app.batch_intents import build_batch_intents
from pypeeker.refactor.intents import ExtractVariableIntent, FixIntent, RenameIntent


class TestValidationErrors:
    """Each malformed shape raises ValueError naming the offending entry."""

    def test_entries_must_be_a_list(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(ValueError, match="JSON list"):
            build_batch_intents({"not": "a list"}, store, store.project_root)

    def test_entry_must_be_an_object(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(ValueError, match=r"intent #1 must be a JSON object"):
            build_batch_intents(["not-an-object"], store, store.project_root)

    def test_unknown_kind_is_rejected(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(ValueError, match="unknown kind"):
            build_batch_intents(
                [{"kind": "levitate"}], store, store.project_root
            )

    def test_duplicate_intent_id_is_rejected(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\ny = 2\n"})
        entries = [
            {
                "kind": "rename",
                "id": "dup",
                "symbol_id": "mod:x",
                "new_name": "x2",
            },
            {
                "kind": "rename",
                "id": "dup",
                "symbol_id": "mod:y",
                "new_name": "y2",
            },
        ]
        with pytest.raises(ValueError, match="duplicate intent id 'dup'"):
            build_batch_intents(entries, store, store.project_root)

    def test_deps_must_be_a_list_of_strings(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        entries = [
            {
                "kind": "rename",
                "symbol_id": "mod:x",
                "new_name": "x2",
                "deps": "not-a-list",
            }
        ]
        with pytest.raises(ValueError, match="'deps' must be a list of intent ids"):
            build_batch_intents(entries, store, store.project_root)

    def test_missing_required_string_field(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        entries = [{"kind": "rename", "symbol_id": "mod:x"}]  # no new_name
        with pytest.raises(ValueError, match="missing or invalid 'new_name'"):
            build_batch_intents(entries, store, store.project_root)

    def test_bad_position_shape_is_rejected(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        entries = [
            {
                "kind": "extract-variable",
                "file_path": "mod.py",
                "start": "not-a-position",
                "end": [0, 5],
                "new_name": "v",
            }
        ]
        with pytest.raises(ValueError, match="'start' must be a 'line:col'"):
            build_batch_intents(entries, store, store.project_root)


class TestHappyPathAndDeps:
    """One intent per supported kind, plus dep resolution across a fix expansion."""

    def test_rename_entry_builds_a_rename_intent(self, indexed_project):
        _, store = indexed_project({"mod.py": "def helper():\n    return 1\n"})
        entries = [
            {
                "kind": "rename",
                "id": "r1",
                "symbol_id": "mod:helper",
                "new_name": "helper2",
                "include_exports": True,
            }
        ]
        [intent] = build_batch_intents(entries, store, store.project_root)
        assert isinstance(intent, RenameIntent)
        assert intent.intent_id == "r1"
        assert intent.symbol_id == "mod:helper"
        assert intent.new_name == "helper2"
        assert intent.include_exports is True

    def test_default_id_is_kind_and_position(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f():\n    x = 1 + 2\n    return x\n"}
        )
        entries = [
            {
                "kind": "extract-variable",
                "file_path": "mod.py",
                "start": "1:8",
                "end": "1:13",
                "new_name": "total",
            }
        ]
        [intent] = build_batch_intents(entries, store, store.project_root)
        assert isinstance(intent, ExtractVariableIntent)
        assert intent.intent_id == "extract-variable-1"

    def test_dep_on_a_fix_entry_resolves_to_every_expanded_intent(
        self, indexed_project
    ):
        # unused-imports flags two unused bindings in one file; the "fix"
        # entry expands into two FixIntents, and a dependent entry's "deps"
        # naming the fix entry's id must resolve to BOTH expanded ids.
        _, store = indexed_project(
            {
                "src/mod.py": (
                    "import os\n"
                    "from typing import Optional\n"
                    "\n"
                    "def f(x):\n"
                    "    return x\n"
                ),
                "src/other.py": "def helper():\n    return 1\n",
            }
        )
        entries = [
            {"kind": "fix", "id": "cleanup", "rule": "unused-imports"},
            {
                "kind": "rename",
                "id": "after-cleanup",
                "symbol_id": "other:helper",
                "new_name": "helper2",
                "deps": ["cleanup"],
            },
        ]
        intents = build_batch_intents(entries, store, store.project_root)

        fix_intents = [i for i in intents if isinstance(i, FixIntent)]
        rename_intents = [i for i in intents if isinstance(i, RenameIntent)]
        assert len(fix_intents) == 2
        assert {i.intent_id for i in fix_intents} == {"cleanup-1", "cleanup-2"}
        [rename] = rename_intents
        assert rename.deps == {"cleanup-1", "cleanup-2"}

    def test_fix_kind_with_no_matching_violations_expands_to_nothing(
        self, indexed_project
    ):
        _, store = indexed_project({"src/mod.py": "def helper():\n    return 1\n"})
        entries = [{"kind": "fix", "rule": "unused-imports"}]
        assert build_batch_intents(entries, store, store.project_root) == []
