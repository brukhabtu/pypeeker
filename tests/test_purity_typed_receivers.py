"""Tests for type-aware receiver classification (TASK-14).

When a receiver root has a normalizable type annotation, the purity check
matches the leaf method against a type-specific denylist instead of the
generic receiver-kind dispatch. These tests pin down that behavior across
the common annotation shapes seen in real Python code.
"""

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    AnalysisContext,
    EvidenceKind,
    PurityChecker,
    PurityVerdict,
)
from pypeeker.analysis.context import _bare_type_name


@pytest.mark.parametrize(
    "annotation,expected",
    [
        ("Path", "Path"),
        ("pathlib.Path", "Path"),
        ("Path | None", "Path"),
        ("Optional[Path]", "Path"),
        ("Union[Path, str]", "Path"),
        ("list[int]", "list"),
        ("dict[str, int]", "dict"),
        ("IO[str]", "IO"),
        ("Optional[pathlib.Path]", "Path"),
        ("None", "None"),  # bare None passes through
        (None, None),
        ("", None),
    ],
)
def test_bare_type_name_extraction(annotation, expected):
    assert _bare_type_name(annotation) == expected


class TestTypedParameterReceivers:
    def test_path_param_write_text_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f(p: Path):\n"
                "    p.write_text('x')\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.kind == EvidenceKind.CALLS_IMPURE_METHOD
            and e.target == "write_text"
            for e in result.evidence
        )

    def test_path_param_pure_methods_stay_pure(self, indexed_project):
        # ``Path.with_suffix`` and ``Path.name`` are pure — should not flag.
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f(p: Path):\n"
                "    return p.with_suffix('.bak').name\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.PROBABLY_PURE
        assert result.evidence == []

    def test_optional_path_param_is_recognized(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "from typing import Optional\n"
                "def f(p: Optional[Path]):\n"
                "    p.unlink()\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.target == "unlink" for e in result.evidence
        )

    def test_pep604_union_param_is_recognized(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f(p: Path | None):\n"
                "    p.unlink()\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE


class TestTypedLocalReceivers:
    def test_typed_local_path_write_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f():\n"
                "    p: Path = Path('/tmp/x')\n"
                "    p.write_text('y')\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.target == "write_text" for e in result.evidence
        )

    def test_untyped_local_string_replace_stays_pure(self, indexed_project):
        # Without type info on `s`, str.replace is correctly treated as pure
        # (replace is no longer in IO_METHOD_NAMES — TASK-16 — and the
        # receiver-type dispatch only fires when type is known).
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    s = 'hello'\n"
                "    return s.replace('h', 'H')\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.PROBABLY_PURE


class TestTypedLogger:
    def test_logger_param_info_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "import logging\n"
                "def f(log: logging.Logger):\n"
                "    log.info('hello')\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        assert result.verdict == PurityVerdict.IMPURE
        assert any(
            e.target == "info" for e in result.evidence
        )


class TestUnknownTypesFallThrough:
    def test_custom_class_param_falls_back_to_kind_dispatch(self, indexed_project):
        # `MyThing` is not in TYPE_IMPURE_METHODS, so we fall back to the
        # generic structural rules — parameter mutation is impure.
        _, store = indexed_project({
            "mod.py": (
                "def f(x: MyThing):\n"
                "    x.append(1)\n"
            )
        })
        result = PurityChecker(store).check("mod.py:f")
        # PARAMETER receiver -> all flagged regardless of type knowledge.
        assert result.verdict == PurityVerdict.IMPURE


class TestContextLocalTypeNames:
    def test_context_captures_param_and_local_types(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f(p: Path, items: list[int]):\n"
                "    local: Path = p\n"
                "    untyped = 1\n"
                "    return p\n"
            )
        })
        ctx = AnalysisContext.for_function(store, "mod.py:f")
        assert "mod.py:f:p" in ctx.local_type_names
        assert ctx.local_type_names["mod.py:f:p"] == "Path"
        assert ctx.local_type_names["mod.py:f:items"] == "list"
        assert ctx.local_type_names["mod.py:f:local"] == "Path"
        # Untyped symbol does not appear.
        assert "mod.py:f:untyped" not in ctx.local_type_names
