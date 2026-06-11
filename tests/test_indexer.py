"""Tests for pypeeker.indexer."""

from __future__ import annotations

import pytest

from pypeeker.indexer import (
    PathNotFoundError,
    ensure_fresh,
    find_project_root,
    index_path,
)
from pypeeker.storage import IndexStore


class TestFindProjectRoot:
    def test_finds_semantic_tool_dir(self, tmp_path):
        (tmp_path / ".semantic-tool").mkdir()
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_project_root(nested) == tmp_path

    def test_finds_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        nested = tmp_path / "a"
        nested.mkdir()
        assert find_project_root(nested) == tmp_path

    def test_finds_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert find_project_root(tmp_path) == tmp_path

    def test_returns_origin_when_no_marker(self, tmp_path):
        # tmp_path is inside the user's filesystem; ensure we don't walk
        # past it by giving it its own non-project sub-dir.
        sub = tmp_path / "lonely"
        sub.mkdir()
        # The repo root is somewhere above tmp_path so we may climb to it.
        # All we can promise: result is sub or an ancestor.
        result = find_project_root(sub)
        assert result == sub or sub.is_relative_to(result)

    def test_prefers_nearest_marker(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / ".git").mkdir()
        nested = inner / "deep"
        nested.mkdir()
        assert find_project_root(nested) == inner


class TestIndexPath:
    def test_index_single_file(self, project_dir):
        (project_dir / "a.py").write_text("x = 1\n")
        store = IndexStore(project_dir)

        result = index_path(project_dir / "a.py", store=store, root=project_dir)

        assert result.indexed == ["a.py"]
        assert result.skipped == []
        assert result.errors == []

    def test_index_directory_recurses(self, project_dir):
        (project_dir / "src").mkdir()
        (project_dir / "src" / "a.py").write_text("x = 1\n")
        (project_dir / "src" / "sub").mkdir()
        (project_dir / "src" / "sub" / "b.py").write_text("y = 2\n")
        store = IndexStore(project_dir)

        result = index_path(project_dir / "src", store=store, root=project_dir)

        assert sorted(result.indexed) == ["src/a.py", "src/sub/b.py"]

    def test_skips_unchanged(self, project_dir):
        (project_dir / "a.py").write_text("x = 1\n")
        store = IndexStore(project_dir)
        index_path(project_dir / "a.py", store=store, root=project_dir)

        result = index_path(project_dir / "a.py", store=store, root=project_dir)
        assert result.indexed == []
        assert result.skipped == ["a.py"]

    def test_collects_per_file_errors(self, project_dir, monkeypatch):
        good = project_dir / "good.py"
        good.write_text("x = 1\n")
        bad = project_dir / "bad.py"
        bad.write_text("y = 2\n")
        store = IndexStore(project_dir)

        # Force a per-file failure on bad.py only.
        from pypeeker import indexer as indexer_mod
        real_bind = indexer_mod.bind

        def fake_bind(adapter, relative, source, root_node, module_path=None):
            if relative.endswith("bad.py"):
                raise RuntimeError("boom")
            return real_bind(adapter, relative, source, root_node)

        monkeypatch.setattr(indexer_mod, "bind", fake_bind)

        result = index_path(project_dir, store=store, root=project_dir)

        assert "good.py" in result.indexed
        assert any(e["file"] == "bad.py" and "boom" in e["error"] for e in result.errors)

    def test_raises_on_missing_target(self, project_dir):
        store = IndexStore(project_dir)
        with pytest.raises(PathNotFoundError):
            index_path(project_dir / "nope.py", store=store, root=project_dir)

    def test_uses_path_outside_root_as_absolute_string(self, tmp_path, project_dir):
        # project_dir IS tmp_path, so put "outside" beside it.
        outside_root = tmp_path.parent
        outside = outside_root / "outside.py"
        outside.write_text("x = 1\n")
        try:
            store = IndexStore(project_dir)
            result = index_path(outside, store=store, root=project_dir)
            assert result.indexed == [str(outside)]
        finally:
            outside.unlink(missing_ok=True)


class TestEnsureFresh:
    def _indexed(self, project_dir, files):
        store = IndexStore(project_dir)
        for name, content in files.items():
            path = project_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            index_path(path, store=store, root=project_dir)
        return store

    def test_reindexes_stale_files(self, project_dir):
        store = self._indexed(project_dir, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
        (project_dir / "a.py").write_text("def f(): pass\n")

        result = ensure_fresh(store, project_dir)

        assert result.indexed == ["a.py"]
        assert result.skipped == ["b.py"]
        assert result.removed == []
        assert not store.is_stale("a.py")
        index = store.load("a.py")
        assert any(s.name == "f" for s in index.symbols)

    def test_removes_entries_for_deleted_sources(self, project_dir):
        store = self._indexed(project_dir, {"a.py": "x = 1\n", "gone.py": "y = 2\n"})
        (project_dir / "gone.py").unlink()

        result = ensure_fresh(store, project_dir)

        assert result.removed == ["gone.py"]
        assert result.skipped == ["a.py"]
        assert store.load("gone.py") is None
        assert store.list_indexed_files() == ["a.py"]

    def test_never_indexed_project_is_a_noop(self, project_dir):
        (project_dir / "a.py").write_text("x = 1\n")
        store = IndexStore(project_dir)

        result = ensure_fresh(store, project_dir)

        assert result.to_dict() == {
            "indexed": [],
            "skipped": [],
            "errors": [],
            "removed": [],
        }
        assert store.load("a.py") is None

    def test_does_not_widen_indexed_set(self, project_dir):
        store = self._indexed(project_dir, {"a.py": "x = 1\n"})
        (project_dir / "never_indexed.py").write_text("z = 3\n")

        result = ensure_fresh(store, project_dir)

        assert result.indexed == []
        assert store.list_indexed_files() == ["a.py"]

    def test_collects_per_file_errors(self, project_dir, monkeypatch):
        store = self._indexed(project_dir, {"a.py": "x = 1\n"})
        (project_dir / "a.py").write_text("x = 2\n")

        from pypeeker import indexer as indexer_mod

        def fake_bind(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(indexer_mod, "bind", fake_bind)

        result = ensure_fresh(store, project_dir)

        assert result.indexed == []
        assert any(
            e["file"] == "a.py" and "boom" in e["error"] for e in result.errors
        )
