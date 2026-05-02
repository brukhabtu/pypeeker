"""End-to-end purity tests against pypeeker's own indexed source.

The killer regression test: behavior on real, non-trivial Python code rather
than synthetic fixtures. Skipped if no project-root index is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypeeker.analysis import (
    AttributeMethodCall,
    ModuleCall,
    is_pure,
    purity,
)
from pypeeker.storage.store import IndexStore

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = REPO_ROOT / ".semantic-tool" / "index" / "src" / "pypeeker"


@pytest.fixture(scope="module")
def project_store():
    if not INDEX_DIR.exists():
        pytest.skip(f"No project index at {INDEX_DIR}; run `pypeeker index src/` first.")
    return IndexStore(REPO_ROOT)


@pytest.mark.parametrize(
    "symbol_id, expected_method",
    [
        ("src/pypeeker/storage/store.py:IndexStore.save", "write_text"),
        ("src/pypeeker/storage/store.py:IndexStore.remove", "unlink"),
        ("src/pypeeker/storage/store.py:IndexStore.save_transaction", "mkdir"),
        ("src/pypeeker/storage/store.py:IndexStore.compute_file_hash", "read_bytes"),
        ("src/pypeeker/refactor/applier.py:TransactionApplier.apply", "read_bytes"),
        ("src/pypeeker/refactor/applier.py:TransactionApplier._apply_file_rename", "mkdir"),
        ("src/pypeeker/refactor/applier.py:TransactionApplier._reindex_files", "read_bytes"),
    ],
)
def test_known_impure_functions_are_flagged(project_store, symbol_id, expected_method):
    obs = purity(project_store, symbol_id)
    assert obs is not None, f"{symbol_id} couldn't be analyzed"
    assert obs, f"{symbol_id} should be impure but observations were empty"

    method_targets = {
        o.method for o in obs if isinstance(o, AttributeMethodCall)
    }
    module_targets = {
        o.qualified_name for o in obs if isinstance(o, ModuleCall)
    }
    matches = (
        expected_method in method_targets
        or any(t.endswith(f".{expected_method}") for t in module_targets)
    )
    assert matches, (
        f"{symbol_id} did not produce evidence for {expected_method!r}; "
        f"got methods={method_targets} qualified={module_targets}"
    )
    assert is_pure(project_store, symbol_id) is False


@pytest.mark.parametrize(
    "symbol_id",
    [
        "src/pypeeker/storage/store.py:IndexStore.project_root",
        "src/pypeeker/storage/store.py:IndexStore._source_to_index_path",
        "src/pypeeker/refactor/applier.py:TransactionApplier._apply_edits_to_content",
    ],
)
def test_known_pure_functions_are_not_flagged(project_store, symbol_id):
    obs = purity(project_store, symbol_id)
    assert obs is not None and not obs, (
        f"{symbol_id} should be pure but got {obs}"
    )
    assert is_pure(project_store, symbol_id) is True
