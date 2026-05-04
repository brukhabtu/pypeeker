"""Tests for type-aware receiver classification.

When a receiver root has a normalizable type annotation, the is_pure
composition matches the leaf method against a type-specific denylist
instead of the generic receiver-kind dispatch.
"""

from __future__ import annotations

import pytest

from pypeeker.analysis import (
    AnalysisContext,
    AttributeMethodCall,
    is_pure,
    is_pure,
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
        ("None", "None"),
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
        obs = is_pure(store, "mod.py:f")
        assert obs is not None
        assert any(
            isinstance(o, AttributeMethodCall) and o.method == "write_text"
            for o in obs
        )

    def test_path_param_pure_methods_stay_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f(p: Path):\n"
                "    return p.with_suffix('.bak').name\n"
            )
        })
        _r = is_pure(store, "mod.py:f"); assert _r is not None and not _r

    def test_optional_path_param_is_recognized(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "from typing import Optional\n"
                "def f(p: Optional[Path]):\n"
                "    p.unlink()\n"
            )
        })
        obs = is_pure(store, "mod.py:f")
        assert obs is not None
        assert any(
            isinstance(o, AttributeMethodCall) and o.method == "unlink"
            for o in obs
        )

    def test_pep604_union_param_is_recognized(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "from pathlib import Path\n"
                "def f(p: Path | None):\n"
                "    p.unlink()\n"
            )
        })
        assert bool(is_pure(store, "mod.py:f"))


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
        obs = is_pure(store, "mod.py:f")
        assert obs is not None
        assert any(
            isinstance(o, AttributeMethodCall) and o.method == "write_text"
            for o in obs
        )

    def test_untyped_local_string_replace_stays_pure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f():\n"
                "    s = 'hello'\n"
                "    return s.replace('h', 'H')\n"
            )
        })
        _r = is_pure(store, "mod.py:f"); assert _r is not None and not _r


class TestTypedLogger:
    def test_logger_param_info_is_impure(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "import logging\n"
                "def f(log: logging.Logger):\n"
                "    log.info('hello')\n"
            )
        })
        obs = is_pure(store, "mod.py:f")
        assert obs is not None
        assert any(
            isinstance(o, AttributeMethodCall) and o.method == "info"
            for o in obs
        )


class TestUnknownTypesFallThrough:
    def test_custom_class_param_falls_back_to_kind_dispatch(self, indexed_project):
        _, store = indexed_project({
            "mod.py": "def f(x: MyThing):\n    x.append(1)\n"
        })
        # PARAMETER receiver -> all flagged regardless of type knowledge.
        assert bool(is_pure(store, "mod.py:f"))


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
        assert ctx.local_type_names["mod.py:f:p"] == "Path"
        assert ctx.local_type_names["mod.py:f:items"] == "list"
        assert ctx.local_type_names["mod.py:f:local"] == "Path"
        assert "mod.py:f:untyped" not in ctx.local_type_names
