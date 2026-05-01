"""End-to-end purity tests against pypeeker's own indexed source.

This is the killer regression test: it asserts behavior on real, non-trivial
Python code rather than synthetic fixtures. It catches the exact class of
false-negative we hit with the original heuristic-only purity checker —
where pathlib I/O on local-variable receivers was silently marked pure.

Tests are skipped if no project-root index is available (e.g. fresh CI
checkout without a .semantic-tool/ directory).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypeeker.analysis import EvidenceKind, PurityVerdict, check_purity
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
    """Real impure functions in pypeeker's own source must be flagged.

    Each parameter pairs a known-impure function with at least one method
    call we expect to appear as evidence. The function may legitimately
    have additional evidence items (multiple I/O calls, attribute writes,
    etc.) — we only assert the specific method shows up.
    """
    result = check_purity(project_store, symbol_id)
    assert result.verdict == PurityVerdict.IMPURE, (
        f"{symbol_id} should be IMPURE but got {result.verdict.value}; "
        f"evidence: {result.evidence}"
    )
    method_targets = {
        e.target for e in result.evidence
        if e.kind == EvidenceKind.CALLS_IMPURE_METHOD
    }
    module_targets = {
        e.target for e in result.evidence
        if e.kind == EvidenceKind.CALLS_IMPURE_MODULE
    }
    matches = (
        expected_method in method_targets
        or any(t and t.endswith(f".{expected_method}") for t in module_targets)
    )
    assert matches, (
        f"{symbol_id} did not produce evidence for {expected_method!r}; "
        f"got method_targets={method_targets} module_targets={module_targets}"
    )


@pytest.mark.parametrize(
    "symbol_id",
    [
        "src/pypeeker/storage/store.py:IndexStore.project_root",
        "src/pypeeker/storage/store.py:IndexStore._source_to_index_path",
        "src/pypeeker/refactor/applier.py:TransactionApplier._apply_edits_to_content",
    ],
)
def test_known_pure_functions_are_not_flagged(project_store, symbol_id):
    """Real pure functions in pypeeker's own source must not be flagged.

    These are deliberately chosen for their lack of any I/O, mutation, or
    non-determinism. Any false positive here means the check has gotten
    too aggressive.
    """
    result = check_purity(project_store, symbol_id)
    assert result.verdict == PurityVerdict.PROBABLY_PURE, (
        f"{symbol_id} should be PROBABLY_PURE but got {result.verdict.value}; "
        f"evidence: {result.evidence}"
    )
    assert result.evidence == []
