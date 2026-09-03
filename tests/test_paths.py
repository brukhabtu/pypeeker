"""Unit tests for the leaf path helpers (pypeeker.paths)."""

import pytest

from pypeeker.paths import is_barrel_path, module_path_from


class TestModulePathFrom:
    def test_strips_src_root_and_suffix(self):
        assert module_path_from("src/pkg/mod.py", ("src",)) == "pkg.mod"

    def test_collapses_init_to_package(self):
        assert module_path_from("src/pkg/__init__.py", ("src",)) == "pkg"

    def test_root_init_is_empty(self):
        assert module_path_from("__init__.py") == ""


class TestIsBarrelPath:
    @pytest.mark.parametrize(
        "path",
        ["__init__.py", "pkg/__init__.py", "src/pkg/sub/__init__.py", "pkg\\__init__.py"],
    )
    def test_true_for_package_init(self, path):
        assert is_barrel_path(path) is True

    @pytest.mark.parametrize(
        "path",
        ["pkg/mod.py", "pkg/__init__.pyi", "pkg/__init__/mod.py", ""],
    )
    def test_false_otherwise(self, path):
        assert is_barrel_path(path) is False
