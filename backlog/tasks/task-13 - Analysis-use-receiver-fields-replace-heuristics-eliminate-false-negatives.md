---
id: TASK-13
title: 'Analysis: use receiver fields, replace heuristics, eliminate false negatives'
status: Done
assignee: []
created_date: '2026-05-01 22:26'
updated_date: '2026-05-01 22:35'
labels: []
dependencies:
  - TASK-12
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rewrite the call-fact extractors and purity check to consume the new receiver_root_symbol_id / receiver_chain fields. Replace the same-line-read heuristic and the local-variable suppression with structural checks on the receiver root. Add a typed denylist keyed on full external names.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New fact: ModuleCall (full_name, line) — fires when receiver_root resolves to an IMPORT symbol and 'imported_from + chain + leaf' is in the denylist
- [x] #2 find_attribute_method_calls reworked to use receiver_root_symbol_id directly instead of same-line-read intersection
- [x] #3 Local-variable suppression replaced by: skip when receiver root is a VARIABLE (no type info), flag when receiver root is a PARAMETER (caller-visible), flag when receiver root is an IMPORT and the full name is in the typed-receiver denylist
- [x] #4 New keyed denylist module replacing the flat IMPURE_ATTRIBUTE_NAMES (e.g. {'os.system': IMPURE, 'pathlib.Path.write_text': IMPURE, 'os.path.join': PURE})
- [x] #5 Re-running check_purity against pypeeker's own src flips IndexStore.save, IndexStore.remove, IndexStore.save_transaction, and TransactionApplier._reindex_files from PROBABLY_PURE to IMPURE
- [x] #6 Existing fact-layer and check-layer tests updated/extended; full suite stays green
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Reworked the analysis layer to consume the receiver_root_symbol_id and receiver_chain fields produced by TASK-12. Replaces the same-line-read heuristic and the local-variable suppression with structural receiver-kind checks.

Changes:
- New ModuleCall fact + find_module_calls extractor: when receiver root resolves to an IMPORT, computes 'imported_from + chain[1:] + leaf' (using imported_from rather than the local name catches aliased imports like 'import os as o') and matches against MODULE_IMPURE_NAMES exact set.
- AttributeMethodCall reworked: now carries a ReceiverKind enum (IMPORT, PARAMETER, VARIABLE, SELF, UNKNOWN) derived from the receiver root's symbol kind. Module-rooted calls excluded (covered by ModuleCall).
- Three method denylists in _purity_denylists.py: MODULE_IMPURE_NAMES (full names like os.system, pathlib.Path.write_text), IO_METHOD_NAMES (impure on any receiver: write_text, read_bytes, unlink, send, recv, ...), COLLECTION_MUTATION_NAMES (impure only on params/unknown receivers: append, extend, pop, ...).
- Purity check policy: PARAMETER -> all flagged; VARIABLE -> only IO methods flagged (collection mutations are pure-local); SELF -> only IO methods (attribute_write fact handles state mutations); UNKNOWN -> only IO methods (conservative).
- Two new EvidenceKind values: CALLS_IMPURE_MODULE and CALLS_IMPURE_METHOD (replacing the old CALLS_IMPURE_STDLIB).

End-to-end validation against pypeeker's own indexed source (tests/test_purity_self.py, 10 parametrized cases):
- IndexStore.save -> IMPURE (catches mkdir + write_text)
- IndexStore.remove -> IMPURE (unlink)
- IndexStore.save_transaction -> IMPURE (mkdir + 3x write)
- IndexStore.compute_file_hash -> IMPURE (read_bytes)
- TransactionApplier.apply -> IMPURE (read_bytes, write_bytes, replace, 2x remove)
- TransactionApplier._apply_file_rename -> IMPURE (mkdir, rename)
- TransactionApplier._reindex_files -> IMPURE (read_bytes)
- IndexStore.project_root, IndexStore._source_to_index_path, TransactionApplier._apply_edits_to_content -> PROBABLY_PURE with empty evidence

All 7 false negatives from before TASK-12+13 have flipped to IMPURE; all 3 known-pure functions stayed pure.

Full suite: 232/232 passing (was 217 pre-13). Updated test_analysis_facts.py and test_purity.py for the new API; added test_purity_self.py for the e2e cases.
<!-- SECTION:FINAL_SUMMARY:END -->
