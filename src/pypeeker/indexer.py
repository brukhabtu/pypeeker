"""Indexing entry points: project-root discovery and bulk-file indexing.

Kept separate from :mod:`pypeeker.cli` so the same logic is usable from
library callers (tests, the future ``check`` command, programmatic users)
without going through Click.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.binder import bind
from pypeeker.paths import module_path_from
from pypeeker.project import load_src_roots
from pypeeker.storage import IndexStore

PROJECT_MARKERS: tuple[str, ...] = (".semantic-tool", "pyproject.toml", ".git")


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: cwd) looking for a project marker.

    Returns ``start`` itself when nothing is found, matching the prior CLI
    behaviour of "use the current directory as a fallback".
    """
    origin = start if start is not None else Path.cwd()
    for directory in [origin, *origin.parents]:
        for marker in PROJECT_MARKERS:
            if (directory / marker).exists():
                return directory
    return origin


@dataclass
class _IndexResult:
    """Outcome of an ``index_path``/``ensure_fresh`` run, grouped by status."""

    indexed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON output."""
        return {
            "indexed": self.indexed,
            "skipped": self.skipped,
            "errors": self.errors,
            "removed": self.removed,
        }


class PathNotFoundError(FileNotFoundError):
    """Raised when ``index_path`` is given a target that isn't a file or dir."""


def index_path(
    target: Path,
    *,
    store: IndexStore,
    root: Path,
    adapter: PythonAdapter | None = None,
    src_roots: tuple[str, ...] | None = None,
) -> _IndexResult:
    """Index every ``.py`` file at or under ``target``.

    Files whose hash matches the saved index are skipped. Per-file failures
    are collected into ``result.errors`` so one bad file doesn't abort the run.

    ``src_roots`` map file paths to dotted module paths for symbol ids; when
    omitted they're read from the project's ``pyproject.toml``.
    """
    if not (target.is_file() or target.is_dir()):
        raise PathNotFoundError(str(target))

    adapter = adapter or PythonAdapter()
    if src_roots is None:
        src_roots = load_src_roots(root)
    files = [target] if target.is_file() else sorted(target.rglob("*.py"))
    result = _IndexResult()

    for file_path in files:
        try:
            relative = str(file_path.relative_to(root))
        except ValueError:
            relative = str(file_path)

        if not store.is_stale(relative):
            result.skipped.append(relative)
            continue

        _index_file(
            file_path,
            relative,
            store=store,
            adapter=adapter,
            src_roots=src_roots,
            result=result,
        )

    return result


def ensure_fresh(
    store: IndexStore,
    root: Path,
    *,
    adapter: PythonAdapter | None = None,
    src_roots: tuple[str, ...] | None = None,
) -> _IndexResult:
    """Bring existing index entries back in sync with the working tree.

    Only files that already have an index entry are considered: stale entries
    are re-indexed, and entries whose source file no longer exists are removed.
    This never widens the indexed set — a never-indexed project is a no-op, so
    queries on it keep their "nothing indexed" behaviour rather than triggering
    a surprise full index.
    """
    result = _IndexResult()
    indexed_files = store.list_indexed_files()
    if not indexed_files:
        return result

    adapter = adapter or PythonAdapter()
    if src_roots is None:
        src_roots = load_src_roots(root)

    for relative in indexed_files:
        source_file = root / relative
        if not source_file.is_file():
            store.remove(relative)
            result.removed.append(relative)
            continue
        if not store.is_stale(relative):
            result.skipped.append(relative)
            continue
        _index_file(
            source_file,
            relative,
            store=store,
            adapter=adapter,
            src_roots=src_roots,
            result=result,
        )

    return result


def _index_file(
    file_path: Path,
    relative: str,
    *,
    store: IndexStore,
    adapter: PythonAdapter,
    src_roots: tuple[str, ...],
    result: _IndexResult,
) -> None:
    """Parse, bind, and save one file, recording the outcome on ``result``."""
    try:
        source = file_path.read_bytes()
        tree = adapter.parse(source)
        module_path = module_path_from(relative, src_roots)
        file_index = bind(
            adapter, relative, source, tree.root_node, module_path=module_path
        )
        store.save(file_index)
        result.indexed.append(relative)
    except Exception as e:
        result.errors.append({"file": relative, "error": str(e)})
