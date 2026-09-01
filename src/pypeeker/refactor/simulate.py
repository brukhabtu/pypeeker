"""Disk-free re-binding for overlay-store simulation.

:class:`pypeeker.storage.overlay.OverlayIndexStore` is pure storage: it may
only import ``models`` under the project's import boundaries, so it cannot
parse or bind source itself. Re-binding overlay content needs the adapter,
the binder, and project config — all of which the ``refactor`` package is
allowed to import — so the convenience lives here as a thin helper.

``pypeeker.indexer._index_file`` is the existing per-file bind helper, but it
is disk-coupled (it reads bytes via ``file_path.read_bytes()`` and reports
into an :class:`IndexResult`), so it cannot serve overlay bytes;
:func:`rebind_source` mirrors its parse → bind → save sequence over bytes the
caller already holds — the batch simulator's freshly spliced content, or the
applier's just-written file — so one sequence serves every substrate.
"""

from __future__ import annotations

from pypeeker.adapters import PythonAdapter
from pypeeker.binder import bind
from pypeeker.models import FileIndex
from pypeeker.paths import module_path_from
from pypeeker.project import load_src_roots
from pypeeker.storage import IndexStore, OverlayIndexStore


def rebind_source(
    store: "IndexStore | OverlayIndexStore",
    source_path: str,
    source: bytes,
    *,
    adapter: PythonAdapter | None = None,
    src_roots: tuple[str, ...] | None = None,
) -> FileIndex:
    """Bind ``source`` as the content of ``source_path`` and save it into ``store``.

    Callers already hold the bytes — the batch simulator hands in the bytes
    it just spliced, the applier the file it just wrote — and pass them
    directly, so one parse → bind → save sequence serves every substrate.
    Any :class:`~pypeeker.storage.IndexStore`-compatible store works — only
    ``project_root`` (for the ``src_roots`` default) and ``save`` are used.

    ``src_roots`` map file paths to dotted module paths for symbol ids; when
    omitted they're read from the project's ``pyproject.toml`` (matching the
    indexer's behaviour).
    """
    adapter = adapter or PythonAdapter()
    if src_roots is None:
        src_roots = load_src_roots(store.project_root)
    tree = adapter.parse(source)
    module_path = module_path_from(source_path, src_roots)
    file_index = bind(
        adapter, source_path, source, tree.root_node, module_path=module_path
    )
    store.save(file_index)
    return file_index

