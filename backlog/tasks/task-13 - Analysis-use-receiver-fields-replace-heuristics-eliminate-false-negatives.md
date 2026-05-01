---
id: TASK-13
title: 'Analysis: use receiver fields, replace heuristics, eliminate false negatives'
status: To Do
assignee: []
created_date: '2026-05-01 22:26'
updated_date: '2026-05-01 22:26'
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
- [ ] #1 New fact: ModuleCall (full_name, line) — fires when receiver_root resolves to an IMPORT symbol and 'imported_from + chain + leaf' is in the denylist
- [ ] #2 find_attribute_method_calls reworked to use receiver_root_symbol_id directly instead of same-line-read intersection
- [ ] #3 Local-variable suppression replaced by: skip when receiver root is a VARIABLE (no type info), flag when receiver root is a PARAMETER (caller-visible), flag when receiver root is an IMPORT and the full name is in the typed-receiver denylist
- [ ] #4 New keyed denylist module replacing the flat IMPURE_ATTRIBUTE_NAMES (e.g. {'os.system': IMPURE, 'pathlib.Path.write_text': IMPURE, 'os.path.join': PURE})
- [ ] #5 Re-running check_purity against pypeeker's own src flips IndexStore.save, IndexStore.remove, IndexStore.save_transaction, and TransactionApplier._reindex_files from PROBABLY_PURE to IMPURE
- [ ] #6 Existing fact-layer and check-layer tests updated/extended; full suite stays green
<!-- AC:END -->
