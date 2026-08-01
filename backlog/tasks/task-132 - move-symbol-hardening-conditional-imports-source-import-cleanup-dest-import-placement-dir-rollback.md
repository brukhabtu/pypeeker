---
id: TASK-132
title: >-
  move-symbol hardening: conditional imports, source import cleanup, dest import
  placement, dir rollback
status: Done
assignee: []
created_date: '2026-08-01 16:17'
updated_date: '2026-08-01 19:54'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Advisories from the D-PR3 adversarial review (all currently correct-but-rough or refuse-too-late): (1) a TYPE_CHECKING-guarded or otherwise conditional import used by the moved body is carried into the destination unguarded — should be refused by name or carried with its guard; (2) import bindings in the source module used only by the moved definition are left dangling (unused-import debt the move itself creates); (3) imports appended to an existing destination land above the appended def mid-file (E402-style) instead of joining the top import block; (4) rolling back a move that created a module in a new directory deletes the file but leaves the empty directory; (5) plan-batch help text does not list the move-symbol kind. Also: the back-imports follow-up (source module importing the moved symbol when remaining code uses it — currently refused by source-module-free).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each numbered advisory either fixed with behavior-pinning tests or explicitly recorded as accepted behavior in architecture.md; gate green; no pre-existing test modified.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Executed via conductor task-pipeline in worktree pypeeker-wt132, in parallel with TASK-134 (file-disjoint).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Back-imports follow-up: DEFERRED, not implemented (2026-08-01).
- source-module-free (refactor/preconditions.py) keeps refusing when the source module still uses the moved symbol.
- It does not wrap cleanly into the existing machinery, on four independent counts: (a) it needs a new opt-in flag to stay inside "refactoring is precise, not clever" (rename's cascades are all opt-in), hence new CLI surface and a new MoveSymbolIntent field, and MoveSymbolIntent.predicted_effect (a rename in id space) has no way to declare a newly CREATED import symbol in the source module, so the batch authorization story would need rethinking; (b) it changes what source-export-list-clean means, since a back-imported name keeps the source's __all__ entry resolving, so that second refusal becomes over-strict and would have to be re-derived; (c) it can manufacture a source<->destination import cycle that this project's own no-import-cycles rule then flags, so the honest version needs cycle detection before it writes -- new analysis; (d) its twin has no symmetric answer: moved-body-closed refuses when the BODY would lose sight of a source-module name, and that direction cannot be repaired by a back-import at all (it would need a forward-import from destination to source, guaranteeing the cycle), so shipping only the source-module-free half makes the refusal surface arbitrary rather than principled.
- architecture.md already records the position ("synthesizing a back-import ... named follow-ups rather than v1"); this note makes the deferral explicit and dated rather than implied.

Advisory (4) did NOT reproduce. TransactionApplier._prune_empty_ancestors already removes conjured directories on rollback, and _remove_dirs does it on the mid-apply failure path; probed via move-symbol -> rollback and --plan -> apply -> rollback, both clean. It lands as a behavior-pinning test (conjured segments gone, pre-existing ancestor and its __init__.py survive, snapshot equality) plus an architecture.md record rather than a code change.
What DOES reproduce is the opposite asymmetry: rollback also prunes an ancestor directory that existed but was EMPTY before the move, because it reconstructs "which directories were conjured" by walking up rather than by replaying a record. Recorded as ACCEPTED and pinned by its own test. Fixing it means persisting the conjured-directory list in the transaction -- an on-disk transaction-format change, out of scope for a hardening task -- and the error it would trade for is worse: under PEP 420 a leftover empty directory is an importable namespace package, so leaving one behind is a semantic difference from the pre-apply tree while removing one is not.
Advisory (2) also lands as ACCEPTED + pinned rather than fixed: the move leaves the source's now-unused imports for the unused-imports rule + check --fix, matching delete-symbol and the fixpoint precedent. Test asserts all three halves (move leaves it, check reports it, check --fix removes exactly it).

Review round (2026-08-01): three confirmed findings on the staged change, all fixed with behavior-pinning tests.
- The mirror direction of advisory (1) is now CLOSED rather than recorded as a gap. `destination-imports-compatible` compared name+`imported_from` against the index, which cannot see a guard, so a destination whose matching binding sat under its own `if TYPE_CHECKING:` was declared present, nothing was written, and the moved body raised NameError at run time (reproduced end to end: exit 0, edit_count 3, `pkg.dest.moved()` -> NameError). It now reads the destination bytes and counts a match present only when the CST proves it a plain module-level statement; guarded or unprovable counts ABSENT, routing the name into the carried set. A different-origin match still refuses as a collision.
- `carried-imports-unconditional` no longer fails open on `root.has_error`. The docstring justification was false: `moved-body-closed` parses only the definition span, so a parse error outside the moved body never trips it. Reproduced with a PEP 696 type-parameter default (a file CPython 3.14 parses and tree-sitter does not) and with a plain syntax error; both wrote the guarded import flat into the destination with exit 0. Evidence is now scoped to the module-level statement the binding lands in, three-valued (top-level / guarded / unproven), so a readable guard in an unreadable file refuses and a readable plain import in one still moves. `unproven` refuses with its own wording.
- `_import_block_anchor` anchored at the end of the LINE holding the last header node, so a semicolon-joined line (`import sys; VALUES = (`) spliced the carried import into the middle of the next statement and wrote an unparseable destination with exit 0 -- a regression against pre-change behavior. The anchor is now a statement boundary: a header node another statement shares a line with ends the run before itself. The milder single-line case (`import sys; VERSION = 1`) also stops producing the E402 advisory (3) exists to eliminate. A trailing `# noqa` is still traversed, not treated as a statement.
Gate after the round: pytest 1924 passed (1915 + 9 new), ruff clean, self-lint exit 0. tests/ diff is additions-only.

Re-review round 2 (2026-08-01): one confirmed finding on the staged change, fixed with behavior-pinning tests.
- Round 1 closed the silent-NameError half of advisory (1) by counting a GUARDED destination binding absent and writing the import for real. That traded a narrow deferred NameError for a broader immediate ImportError: a destination that guards its import of a module which imports it back (the ordinary reason to guard) got a plain `from pkg.cycle import Thing` written beside the surviving `if TYPE_CHECKING:`, closing the cycle. Reproduced end to end -- exit 0, edit_count 3->4, then `import pkg.util` raises `ImportError: cannot import name 'other' from partially initialized module`, breaking the destination and everything importing it, none of which the move named.
- `destination-imports-compatible` now REFUSES when the destination match is proven GUARDED and the source binding is proven TOP_LEVEL -- the combination where no write and a write are both wrong. It reads the source bytes too (new `source_content` argument) to tell that combination from the guarded/guarded one, which still routes into the carried set so `carried-imports-unconditional` gives the message naming the guard to promote. An unproven destination match still counts absent, now justified by what unproven means (the statement does not parse, so the destination cannot be imported and has no run-time guard left to defeat) rather than by the false claim that a duplicated import is inert -- architecture.md`s recorded justification was corrected on that point.
- 4 new tests (net +4): the guarded-destination refusal by name and wording; nothing written, no transaction, no duplicate binding (ruff F811); the cycle fixture proving `import pkg.util` still works after the refusal; and the counterfactual pinning that the bytes round 1 would have written really do raise ImportError.
- Gate: pytest 1928 passed, ruff clean, self-lint exit 0. tests/ diff vs HEAD is additions-only (0 deleted lines under --patience/--histogram).

Executed via conductor pipeline in worktree wt132, parallel with TASK-134. Conductor: opus implementer, plan review ON, 3-way split, 4 lenses (applier-ordering-and-rollback-exactness, refusal-surface-false-positive-and-negative, frozen-payload-and-layering-drift, records-versus-reality), re-review armed. Scout DISPROVED advisory 4 (rollback already prunes created dirs — pinned test exists) and found the OPPOSITE bug: rollback deletes a pre-existing empty directory it never created (recorded as advisory). Landed: CarriedImportsUnconditional refusal for guarded/conditional imports (fail-CLOSED on parse errors after review), correct top-of-file import-block anchoring for extended destinations, dest-guarded-binding double-import prevention (caught by the RE-REVIEW round — the fixer-blind-spot stage working as designed), batch help text, source dangling-import decision recorded (unused-imports + check --fix is the composing remedy; same-batch fix entries cannot see post-move state by construction). Back-imports: principled deferral stands. Review totals: 23 findings across two rounds, 4 must-fix applied. Gate 1928 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
move-symbol hardening (TASK-132), via the conductor pipeline in a parallel worktree.

- Conditional imports: a TYPE_CHECKING/try-except/version-gated import used by the moved body now refuses by name (carried-imports-unconditional, fail-closed on unparseable sources) instead of being silently carried unguarded; a guarded binding at the DESTINATION no longer masquerades as satisfying compatibility (re-review catch — prevented a double-import).
- Destination import placement: carried imports join the top import block via a CST-anchored header scan instead of landing mid-file above the appended def.
- Advisory 4 disproven with evidence (rollback already prunes created dirs); the opposite over-reach (deleting a pre-existing empty dir) recorded as a follow-up advisory.
- Source dangling imports: decision recorded — the unused-imports rule + check --fix is the composing remedy; batch help text updated; back-imports deferral reaffirmed.

23 findings across initial + re-review rounds, 4 must-fix applied. 1928 tests, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
