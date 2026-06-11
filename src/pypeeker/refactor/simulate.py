"""Disk-free re-binding for overlay-store simulation.

:class:`pypeeker.storage.overlay.OverlayIndexStore` is pure storage: it may
only import ``models`` under the project's import boundaries, so it cannot
parse or bind source itself. Re-binding overlay content needs the adapter,
the binder, and project config — all of which the ``refactor`` package is
allowed to import — so the convenience lives here as a thin helper.

``pypeeker.indexer._index_file`` is the existing per-file bind helper, but it
is disk-coupled (it reads bytes via ``file_path.read_bytes()`` and reports
into an :class:`IndexResult`), so it cannot serve overlay bytes; ``rebind``
mirrors its parse → bind → save sequence over :meth:`OverlayIndexStore.read_file`
content instead.
"""

from __future__ import annotations

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.binder.binder import bind
from pypeeker.models.index import FileIndex
from pypeeker.paths import module_path_from
from pypeeker.project import load_src_roots
from pypeeker.storage.overlay import OverlayIndexStore


def rebind(
    store: OverlayIndexStore,
    source_path: str,
    *,
    adapter: PythonAdapter | None = None,
    src_roots: tuple[str, ...] | None = None,
) -> FileIndex:
    """Parse + bind the overlay-visible content of ``source_path`` and save it in-memory.

    Reads bytes through the overlay (so a prior ``write_file`` is what gets
    bound), produces a :class:`FileIndex` via the pure binder, and saves it
    into the overlay's in-memory index layer. Neither the disk nor the base
    store is touched; after this call ``store.is_stale(source_path)`` is False
    until the overlay content changes again.

    ``src_roots`` map file paths to dotted module paths for symbol ids; when
    omitted they're read from the project's ``pyproject.toml`` (matching the
    indexer's behaviour).
    """
    adapter = adapter or PythonAdapter()
    if src_roots is None:
        src_roots = load_src_roots(store.project_root)
    source = store.read_file(source_path)
    tree = adapter.parse(source)
    module_path = module_path_from(source_path, src_roots)
    file_index = bind(
        adapter, source_path, source, tree.root_node, module_path=module_path
    )
    store.save(file_index)
    return file_index
