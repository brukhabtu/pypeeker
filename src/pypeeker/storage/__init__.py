"""Storage layer: per-file index persistence + refactor transaction persistence."""

from pypeeker.storage.baseline import (
    BASELINE_FILE,
    BaselineKeyed,
    baseline_identity,
    baseline_namespaces,
    baseline_path,
    clear_symbol_baseline,
    delta,
    has_symbol_baseline,
    load_baseline,
    load_symbol_baseline,
    write_baseline,
    write_symbol_baseline,
)
from pypeeker.storage.index_store import IndexStore
from pypeeker.storage.overlay import OverlayIndexStore
from pypeeker.storage.protocols import IndexStoreLike, TreeStoreLike
from pypeeker.storage.transaction_store import (
    LoadedTransaction,
    TransactionLoadError,
    TransactionStore,
)
from pypeeker.storage.tree_store import InMemoryTreeStore, TreeStore

__all__ = [
    # baseline ("ratchet") file: violations + born-private symbol namespaces
    "BASELINE_FILE",
    "BaselineKeyed",
    "baseline_identity",
    "baseline_namespaces",
    "baseline_path",
    "clear_symbol_baseline",
    "delta",
    "has_symbol_baseline",
    "load_baseline",
    "load_symbol_baseline",
    "write_baseline",
    "write_symbol_baseline",
    "IndexStore",
    "IndexStoreLike",
    "InMemoryTreeStore",
    "LoadedTransaction",
    "OverlayIndexStore",
    "TransactionLoadError",
    "TransactionStore",
    "TreeStore",
    "TreeStoreLike",
]
