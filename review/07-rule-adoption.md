# 07 — Check rule adoption (TASK-112)

Decision record for which of pypeeker's 21 builtin rules run as self-lint gates
on pypeeker's own source, and why the rest stay opt-in. Follows the fixes in
TASK-110 (PreferTupleFix) and TASK-111 (unused-imports false positives).

## Enabled as gates (11)

`[tool.pypeeker].rules`: the 4 long-standing structural gates —
`require-docstrings`, `no-unresolved-refs`, `import-boundaries`, `barrel-only` —
plus 7 hygiene/correctness rules that report **zero findings at default
confidence** on `src/`, so they gate green today and keep the code clean going
forward:

- `star-imports`
- `import-time-side-effects`
- `docstring-drift`
- `unused-imports` (trustworthy now that TASK-111 fixed its false positives)
- `pure-decorator-contracts`
- `naming-conventions` (the source already follows PEP 8)
- `test-only-production-code`

Each rule's `test_not_in_default_rules` was updated to assert it is now enabled
(the rules genuinely moved to default).

## Deferred, with rationale

Grouped by *why* they are not gated. "No fix tool" means pypeeker has no
refactoring op that resolves the finding (unlike rename/extract/inline/privatize).

### No-tool findings — AC3 decision: keep opt-in (scope), do not gate

- **`no-argument-mutation` (90 findings).** Predominantly the intentional
  accumulator/builder pattern — `_iter_preconditions(state)` in the
  planner/extract/inline flows, `_record_subscript_mutation(state)` in the
  binder, `_append_segment(segments)` in hierarchy — i.e. deliberate
  output-parameter design. No fix tool. Refactoring ~90 sites to
  return-instead-of-mutate is a large, behavior-risking change for a stylistic
  rule. **Decision: keep opt-in.** If gated later, the cleaner path is a rule
  option to allow a designated "builder state" parameter, not a mass refactor.
- **`unused-return-value` (8 findings).** The flagged returns are convenience /
  fluent returns (`ScopeStack.pop`, `declare_in_scope`) or reasonable API whose
  value is consumed by tests but not by `src` — the rule only counts `src` call
  sites (e.g. `IndexStore.save -> Path`, `TransactionStore.save`, `TreeStore.save`).
  Dropping the annotations would degrade a tested API for no real gain. **Decision:
  keep opt-in.**
- **`no-hidden-global-mutation` (2 findings).** Both are
  `register_rule._decorate` writing the module-level `_REGISTERED` /
  `_REGISTERED_PROJECT` registries — the mechanism the entire self-registration
  architecture is built on. Intentional. **Decision: keep opt-in** (gating would
  require exempting the rule registry, which is not worth it for 2 by-design sites).

### Advisory / churny — keep opt-in

- **`prefer-tuple` (41 findings).** Advisory ("could be a tuple"), documented
  opt-in. Its fix is correct now (TASK-110), but adopting it as a gate would
  mean applying ~40 list→tuple rewrites for little value. Deferred (AC4).

### Inert without extra config — not worth gating as-is

- **`no-impure-functions`.** A no-op without an `include` scope; there is no
  purity contract (`*.pure` convention) to point it at yet.
- **`born-private`.** A ratchet that auto-seeds and reports nothing on first
  run; in fresh CI (no committed `.pypeeker/check-baseline.json`, which is
  gitignored) it re-seeds every run and never catches anything. Would need a
  committed baseline to be an effective gate.

### Visibility rules — deferred as a group pending a policy decision

- **`over-exposed-export` (31), `under-exposed-access` (37)** have real findings
  and would need a `privatize`/`promote` cleanup pass first.
- **`unused-public-symbol`** has heuristic (hidden) findings — the ~25 candidates
  from the earlier privatize dogfood (`review/06 §4`).
- **`over-exposed-module-symbol`** reports zero default findings and *could* be
  gated, but is held with its rule family so visibility enforcement is decided
  as one unit (the export/access cleanup is the gating dependency).

**Decision:** treat visibility-rule adoption as a follow-up that first burns
down the export/access findings via `privatize`/`promote`, then gates all four
together. Tracked with the `review/06 §4` privatize decision.
