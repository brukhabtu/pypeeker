"""End-to-end impurities tests against pypeeker's own indexed source.

The killer regression test: behavior on real, non-trivial Python code rather
than synthetic fixtures. Skipped if no project-root index is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypeeker.analysis import (
    AttributeMethodCall,
    ModuleCall,
    impurities,
)
from pypeeker.storage import IndexStore

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
        ("pypeeker.storage.index_store:IndexStore.save", "write_text"),
        ("pypeeker.storage.index_store:IndexStore.remove", "unlink"),
        ("pypeeker.storage.transaction_store:TransactionStore.save", "mkdir"),
        ("pypeeker.storage.index_store:IndexStore.compute_file_hash", "read_bytes"),
        ("pypeeker.refactor.applier:TransactionApplier.apply", "read_bytes"),
        ("pypeeker.refactor.applier:TransactionApplier._apply_file_rename", "mkdir"),
        ("pypeeker.refactor.applier:TransactionApplier._reindex_files", "read_bytes"),
    ],
)
def test_known_impure_functions_are_flagged(project_store, symbol_id, expected_method):
    obs = impurities(project_store, symbol_id)
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
    assert bool(impurities(project_store, symbol_id))


@pytest.mark.parametrize(
    "symbol_id",
    [
        "pypeeker.storage.index_store:IndexStore.project_root",
        "pypeeker.storage.index_store:IndexStore._source_to_index_path",
        "pypeeker.refactor.applier:TransactionApplier._apply_edits_to_content",
    ],
)
def test_known_pure_functions_are_not_flagged(project_store, symbol_id):
    obs = impurities(project_store, symbol_id)
    assert obs is not None and not obs, (
        f"{symbol_id} should be pure but got {obs}"
    )
    _r = impurities(project_store, symbol_id); assert _r is not None and not _r
