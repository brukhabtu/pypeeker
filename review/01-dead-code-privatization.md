# 01 — Dead Code & Privatization: Actionable Target List

Deep-dive of `/home/user/pypeeker`, verifying the prior scan independently and
producing an ordered removal plan. Every claim carries `file:line` evidence.

**Ground truth captured this session:**
- `uv run pypeeker index src` — clean, indexes 70+ files, `errors: []`.
- `uv run pypeeker privatize` (no flags) — **0 executed, 25 skipped, every one with
  `reason: heuristic-confidence`**. The tool refuses to auto-fix *any* nomination today.
- `uv run pypeeker privatize --include-heuristic` — stages a transaction (`tx_id`,
  `edit_count 6`) that renames 22 unique symbols, but **does not touch the working tree**
  (writes only to the gitignored `.semantic-tool/transactions/*.jsonl`; `git status`
  stayed clean throughout). So privatize is plan-only here; nothing was applied.

> Note: privatize's `--include-heuristic` "executed" list reflects a *staged* mirror
> transaction, not applied edits. Confirmed `src/pypeeker/check/rules.py:130` still reads
> `def import_boundaries(` and `src/pypeeker/refactor/batch.py:211` still `class BatchResult:`.

---

## Summary table

| # | Target | Kind | Category | Action | Risk |
|---|--------|------|----------|--------|------|
| 1 | `SEMANTIC_TOOL_DIR` duplicate (`transaction_store.py:24`) | Duplicated const | — | Consolidate onto `index_store.py:15` | Very safe |
| 2 | `_Capability` enum `models/capabilities.py:6` | Dead (test-only) | B | Delete enum + test line 151 + import line 3 | Very safe |
| 3 | `_LanguageAdapter` Protocol `adapters/base.py:31` | Dead (zero referents) | A | Delete class (whole module optional) + fix stale xref | Safe |
| 4 | 7 rule functions (`check/rules.py`, `check/builtin`) | Registry/decorator dispatch | C | **Leave** — dynamic false positives | n/a |
| 5 | `BatchResult`, `DropReason`, `ExecutedIntent` `refactor/batch.py` | Public return surface | D | **Keep public** | n/a |
| 6 | `Schedule`, `schedule` `refactor/batch.py` | Over-exposed (in-module only) | B | Optional privatize | Safe |
| 7 | `materialize_mirror`, `ScheduleCycleError` `refactor/batch.py` | Test-only export | B | Privatize + retarget test import | Needs test edit |
| 8 | `ConflictKind`, `ConflictReport` `refactor/footprint.py` | Over-exposed / test-only | B | Optional privatize | Safe / test edit |
| 9 | `OrphanReason`, `PlannableFix` `refactor/intents.py` | Test-only export | B | Optional privatize + test edit | Needs test edit |
| 10 | **`convention_renames.py` whole module** (4 symbols) | Unwired feature, test-only | B | **Product decision**: wire in or delete module+test | Human judgment |

Nothing found is auto-fixable safely today; every item is either a manual delete
(1–3) or a human/product judgment call (4–10).

---

## 1. Duplicated constant `SEMANTIC_TOOL_DIR` — CONFIRMED

Two identical definitions:
- `src/pypeeker/storage/index_store.py:15` → `SEMANTIC_TOOL_DIR = ".semantic-tool"` (alongside `INDEX_DIR`)
- `src/pypeeker/storage/transaction_store.py:24` → `SEMANTIC_TOOL_DIR = ".semantic-tool"` (alongside `TRANSACTIONS_DIR`)

**Every importer / user of each definition:**

| Consumer | Line | Imports from |
|----------|------|--------------|
| `index_store.py` (self) | `:24` uses it | index_store def |
| `storage/overlay.py` | `:36` `from pypeeker.storage.index_store import INDEX_DIR, SEMANTIC_TOOL_DIR, IndexStore`; used `:127` | index_store def |
| `storage/tree_store.py` | `:13` `from pypeeker.storage.index_store import SEMANTIC_TOOL_DIR`; used `:22` | index_store def |
| `transaction_store.py` (self) | `:34` uses it | transaction_store def |
| `refactor/batch.py` | `:88` `from pypeeker.storage.transaction_store import SEMANTIC_TOOL_DIR`; used `:875` | transaction_store def |

The storage barrel does **not** export it (`storage/__init__.py:8` `__all__ = ["IndexStore", "OverlayIndexStore", "TransactionStore", "TreeStore"]`), so all importers deep-import a submodule. `batch.py:88` is the only *cross-package* importer; the rest are intra-`storage`.

**Recommendation — survivor: `index_store.py:15`.** It is the natural leaf (holds `INDEX_DIR`, imports only `pypeeker.models`), and two of the three external importers already point at it. `index_store` imports nothing from `transaction_store`, so pointing `transaction_store` at `index_store` introduces no cycle.

**Exact rewrites:**
1. Delete `src/pypeeker/storage/transaction_store.py:24` (`SEMANTIC_TOOL_DIR = ".semantic-tool"`).
2. In `transaction_store.py`, import it: `from pypeeker.storage.index_store import SEMANTIC_TOOL_DIR` (intra-package, no barrel needed). Keep `TRANSACTIONS_DIR` local.
3. Change `refactor/batch.py:88` from
   `from pypeeker.storage.transaction_store import SEMANTIC_TOOL_DIR` →
   `from pypeeker.storage.index_store import SEMANTIC_TOOL_DIR`.

Import-boundary/barrel check: `refactor → storage` is already exercised (batch.py:88 today) and `storage → storage` is intra-package; `SEMANTIC_TOOL_DIR` is not in the storage barrel `__all__`, and the `barrel-only` rule only forces barrel routing for symbols the barrel re-exports, so deep-importing it stays legal. Re-run `pypeeker index src && pypeeker check` to confirm.

---

## 2. `_Capability` enum — CONFIRMED DEAD (test-only)

Definition: `src/pypeeker/models/capabilities.py:6` `class _Capability(str, Enum):` (9 members).

**Every reference in the repo** (grep `\bCapability\b`, excluding docs/backlog):
- `tests/test_models.py:3` — `from pypeeker.models.capabilities import _Capability as Capability, Confidence`
- `tests/test_models.py:151` — `assert Capability.VISIBILITY.value == "visibility"`

No production consumer anywhere. `Confidence` (same file, `:20`) is heavily used and must stay. `architecture.md:53,140` and `CLAUDE.md:58` describe it as a reserved roadmap enum with no consumers — matches. This is category **B** (test-only), effectively dead.

**Exact deletion:**
1. Delete `capabilities.py:6–17` (the whole `class _Capability` block; leave `Confidence`).
2. `tests/test_models.py:3` — change to `from pypeeker.models.capabilities import Confidence` (drop `_Capability as Capability,`).
3. `tests/test_models.py:151` — delete the line
   `    assert Capability.VISIBILITY.value == "visibility"`
   (the surrounding `test_enum_serialization` keeps its other 5 asserts on lines 152–156).

Interaction: removing line 151 is required or the test breaks on the missing import. No other test touches it.

---

## 3. `_LanguageAdapter` Protocol — CONFIRMED TRULY DEAD (category A)

Definition: `src/pypeeker/adapters/base.py:31` `class _LanguageAdapter(Protocol):`.

A precise whole-repo scan (bareword occurrences of every top-level def/class/const across `src` + `tests`) found **exactly one symbol whose name appears only at its own definition and nowhere else: `_LanguageAdapter`.** occ = 1.

- Not imported by anyone: grep for `adapters.base` import yields **zero** code importers (only docstring xrefs).
- `PythonAdapter` (`adapters/python_adapter.py:21`) does **not** subclass it — it satisfies the Protocol structurally, so the class object is never referenced.
- `adapters/__init__.py:20` exports only `PythonAdapter`.

It was renamed private by a prior privatization pass (task-69/92/97 history), which orphaned it. **Stale doc xref bug found:** `adapters/__init__.py:14` still says `:class:`~pypeeker.adapters.base.LanguageAdapter`` (no underscore) — a broken Sphinx-style reference to the old public name. (`python_adapter.py:8` refers to the module `pypeeker.adapters.base`, which is fine.)

**Recommendation:** Delete the dead Protocol. Two options:
- **Minimal:** delete `base.py:31–45` (the class + its stub methods), keep the module docstring if you want the prose. But a module that is only a docstring is odd.
- **Cleaner (preferred):** delete the entire `src/pypeeker/adapters/base.py` — its prose is already duplicated almost verbatim in `adapters/__init__.py:1–17`. Then fix `adapters/__init__.py:14` to drop the `base.LanguageAdapter` xref (or reword to describe the boundary without a class reference), and adjust `python_adapter.py:8` which points at `pypeeker.adapters.base` for "the contract".

Risk: Safe — no code path imports the module. Only doc strings mention it; those must be updated in the same change or the self-lint's `docstring`/xref rules could complain.

---

## 3b. Broader dead-code sweep (privatize can't see these)

Method: AST-parsed every top-level `def`/`class`/`UPPER_CONST` in `src`, then counted total bareword occurrences across `src`+`tests`.

- **Only one** non-decorated symbol has occ == 1 (never referenced): `_LanguageAdapter` (item 3 above).
- Decorated symbols with occ == 1 are all **live via decorators** — the 8 Click command funcs in `cli.py` (`plan_extract_variable:475`, `plan_inline_variable:511`, `plan_extract_method:538`, `plan_batch:580`, `plan_rename:745`, `transactions_list:1063`, `transactions_show:1091`, `transactions_cancel:1113`) are registered through `@main.command(...)` / `@transactions.command(...)`. Not dead.
- No unused module-level `UPPER_CONST` found: every constant flagged by the crude "no external importer" pass (e.g. `TRANSACTIONS_DIR` `transaction_store.py:25`, `TREE_FILE` `tree_store.py:15`, `BASELINE_RELPATH` `baseline.py:67`, `MAX_PLAN_ATTEMPTS_PER_INTENT` `batch.py:90`, the `naming_conventions.py` regexes) is used **within its own file** — live, correctly private-ish, not dead.
- No unreachable branches or never-instantiated classes surfaced beyond the above; the private `_`-helpers all resolve to in-module callers.

Conclusion: outside the privatize nominations, the codebase's only genuine dead symbol is `_LanguageAdapter`. It is remarkably clean (it lints itself).

---

## 4. Privatize nominations — full list, classified

Full nomination set = 22 unique symbols (the `--include-heuristic` `executed` list of 22, plus 3 `pending-collision` skips that are duplicates of `pure_decorator_contracts`, `convention_rename_intents`, `write_intents_file`). The no-flag run skips **all** of them as `heuristic-confidence`.

Categories: **A** truly dead · **B** test-only (privatize/underscore or delete-with-test) · **C** false positive (dynamic dispatch — leave) · **D** genuine public API (keep).

### Category C — rule functions reached dynamically (LEAVE)

These are the "false-positive nominations of the rule functions" from the brief. Each is referenced by an in-module **registry dict** or a `@register_rule` **decorator**, then dispatched by name at runtime — invisible to the static unused/over-exposed rules, hence the `heuristic-confidence` (dynamic-access-nearby) skip.

| Symbol | Def | Live reference (dynamic) | Tests using it |
|--------|-----|--------------------------|----------------|
| `require_docstrings` | `check/rules.py:` (REGISTRY val) | `check/rules.py:840` `REQUIRE_DOCSTRINGS: require_docstrings` | `tests/test_check_rules.py:12,19…` |
| `no_unresolved_refs` | `check/rules.py` | `check/rules.py:841` | `tests/test_check_rules.py:11,59,67,90` |
| `prefer_tuple` | `check/rules.py` | `check/rules.py:842` | `tests/test_check_rules.py:519,524`; `test_check_fix.py:30,85…` |
| `import_boundaries` | `check/rules.py:130` | `check/rules.py:846` `IMPORT_BOUNDARIES: import_boundaries` | `tests/test_check_rules.py:10,106` |
| `unused_public_symbol` | `check/rules.py` | `check/rules.py:847` | `test_confidence.py:23`, `test_visibility_config.py:24`, `test_privatize_cli.py:40`, `test_check_rules.py:561` |
| `no_impure_functions` | `check/rules.py` | `check/rules.py:848` | `test_confidence.py:23,129`; `test_check_rules.py:686,702` |
| `pure_decorator_contracts` | `check/builtin/pure_decorator_contracts.py:73` | `@register_rule(PURE_DECORATOR_CONTRACTS, scope="project")` decorator `:72` + `get_project_rule` dispatch (`test_...:226`) | `tests/test_rule_pure_decorator_contracts.py:6,47,226` |

The registry dicts live at `check/rules.py:839` (`REGISTRY`) and `:845` (`PROJECT_REGISTRY`); builtin rules self-register on import via `register_rule` (`check/rules.py:857`, engine side-effect import at `check/engine.py:38`). **Action: leave all 7 public.** This is the known dynamic-use false-positive class (see `backlog/tasks/task-79`, `task-97`). Privatizing them would only churn ~30 test imports for zero benefit.

### Category D — public return surface of `run_batch` (KEEP)

`run_batch` **is** re-exported through the barrel (`refactor/__init__.py:10`) and called by `cli.py:647` and `refactor/privatize.py:594`. These types are the shape of its result; external callers hold instances (`result.executed`, `.dropped`, `.root`) even though they don't import the type *names*. The over-exposed rule flags them because no other module imports the bareword — but they are legitimately public.

| Symbol | Def | Why public |
|--------|-----|-----------|
| `BatchResult` | `refactor/batch.py:211` | return type of public `run_batch` (`:764`) |
| `ExecutedIntent` | `refactor/batch.py:191` | `BatchResult.executed: tuple[ExecutedIntent, ...]` (`:223`) |
| `DropReason` | `refactor/batch.py:141`-region enum | enum on dropped entries (`:368,385,732…`) surfaced via `result.dropped` |

**Action: keep public** (all in `batch.py __all__` at `:929–939`). Optionally add a doc note that they're the `run_batch` result surface. Not test-imported, so privatizing is possible but low-value and would obscure the public result type.

### Category B — over-exposed / test-only (privatize candidates)

Live within their own module but their public *name* is imported only by tests (or by nothing outside the module). Real cleanup candidates; each needs judgment + possible test edits.

| Symbol | Def | In-module use | Cross-module consumer | Sub-class |
|--------|-----|---------------|-----------------------|-----------|
| `Schedule` | `refactor/batch.py:177` | return of `schedule` (`:313,471`) | none (not tests) | over-exposed only |
| `schedule` | `refactor/batch.py:313` | called by `run_batch` (`:701`) | none (not tests) | over-exposed only |
| `materialize_mirror` | `refactor/batch.py` | used `:709` | `tests/test_batch.py:27,547,559,571` | test-only export |
| `ScheduleCycleError` | `refactor/batch.py` | raised `:470` | `tests/test_batch.py:24,243,253` | test-only export |
| `ConflictKind` | `refactor/footprint.py:` (enum) | built by `conflicts_with` (`:221,226…`) | `tests/test_intents.py:21,103,112,120` | test-only export |
| `ConflictReport` | `refactor/footprint.py:112` | return of live `conflicts_with` (`:250`) | none (not tests) | over-exposed only |
| `OrphanReason` | `refactor/intents.py:` (enum) | used `:87,181` | `tests/test_intents.py:34,307,315` | test-only export |
| `PlannableFix` | `refactor/intents.py:` (Protocol) | annotation `fix: PlannableFix` (`:481`) | `tests/test_intents.py:35,475,488` | test-only export |

None of these are re-exported through `refactor/__init__.py` (which surfaces only `BatchPolicy, ScheduleError, run_batch, flatten_batch, BatchAborted, FlattenError` from batch — see `refactor/__init__.py:4–11,31–60`). So privatizing them cannot break any barrel consumer.

- **Over-exposed only** (`Schedule`, `schedule`, `ConflictReport`): safe to rename `_Schedule`/`_schedule`/`_ConflictReport` and drop from their module `__all__`. No test touches them. Note `batch.py` module docstring (`:5`) advertises `schedule` as "pure public" — a deliberate design seam, so this is a mild judgment call, not a slam-dunk.
- **Test-only export** (`materialize_mirror`, `ScheduleCycleError`, `ConflictKind`, `OrphanReason`, `PlannableFix`): privatizing requires updating the cited `tests/` import to `from … import _name as name` aliases (the mechanical pattern task-97 already used across 25 test files). Alternatively keep them public as intentional test seams. Recommend **keep** unless you want the module `__all__` to shrink — the churn/benefit ratio is poor.

### Category B (headline) — `convention_renames.py`: an entire unwired module

`SkipReason`, `SkippedRename`, `convention_rename_intents`, `write_intents_file` (defs in `refactor/convention_renames.py`, `__all__` at `:275–278`).

**No `src` module imports `convention_renames` at all** — grep for `convention_renames` import in `src/` returns zero. Its only importer is `tests/test_convention_renames.py` (`:26,28,29`, exercised at `:78,117,228,243,357`). The `check/builtin/naming_conventions.py:9,159` mentions `convention_rename_intents` **only in docstrings** (`:func:` xrefs), not as a call. The module docstring itself (`convention_renames.py:245`) describes the intended pipeline `check (rule) -> convention_rename_intents -> write_intents_file -> run_batch`, but **that pipeline is never wired**: the live CLI batch path (`cli.py:604–648`) uses `app/batch_intents.build_batch_intents` + `run_batch` + `flatten_batch`, not this converter.

So this is a ~280-line feature that exists and is tested but has **no production entry point**. Category B, strongest case.

**Recommendation — product decision (human judgment):**
- **(a) Wire it in** — if convention-driven mass rename is a real intended workflow, connect `convention_rename_intents`/`write_intents_file` into a CLI command or into `naming_conventions`'s fix path. Then the symbols become genuine public API (D).
- **(b) Delete it** — if it was speculative, remove `src/pypeeker/refactor/convention_renames.py` and `tests/test_convention_renames.py` together, and scrub the two `:func:` xrefs in `naming_conventions.py:9,159`.

Do **not** merely privatize these — that hides a design question. Decide (a) or (b) first.

---

## 5. Ranked, ordered removal plan

Execute top-down. Re-run `uv run pypeeker index src && uv run pypeeker check && uv run pytest -q` after each numbered step.

**Tier 1 — mechanical, no judgment (do first):**
1. **Consolidate `SEMANTIC_TOOL_DIR`** (§1): delete `transaction_store.py:24`, add the intra-package import there, retarget `batch.py:88` to `index_store`. Source-only, no test change. Self-lint must stay green (import-boundaries + barrel-only).
2. **Delete `_Capability`** (§2): remove `capabilities.py:6–17`; edit `tests/test_models.py:3` (drop the alias) and delete `:151`. *Interaction:* the test import and the assert must be removed together or `test_models.py` fails at import.

**Tier 2 — safe delete, touches docstrings (do next):**
3. **Delete `_LanguageAdapter`** (§3): remove the class (preferably the whole `adapters/base.py`), and fix the stale xref `adapters/__init__.py:14` (+ reword `python_adapter.py:8`). *Interaction:* update the docstrings in the same commit so no xref dangles.

**Tier 3 — optional privatization, low risk, source-only (opt-in cleanup):**
4. Privatize the **over-exposed-only** trio `Schedule`, `schedule` (`batch.py:177,313`) and `ConflictReport` (`footprint.py:112`) to `_`-names, dropping them from their `__all__`. No test import to change. Weigh against the `batch.py:5` docstring calling `schedule` a public primitive.

**Tier 4 — needs coordinated test edits (only if shrinking `__all__` is a goal):**
5. Privatize the **test-only exports** `materialize_mirror`, `ScheduleCycleError` (`batch.py`), `ConflictKind` (`footprint.py`), `OrphanReason`, `PlannableFix` (`intents.py`). *Interaction:* each requires the matching `tests/test_batch.py` / `test_intents.py` import to switch to a `from … import _x as x` alias. Recommended default: **leave public** as intentional test seams — churn/benefit is poor.

**Tier 5 — human/product decision (do not automate):**
6. Resolve `convention_renames.py` (§ headline): **wire in** (promotes 4 symbols to public API) **or delete module + its test + scrub `naming_conventions.py:9,159` xrefs**. This is the single largest chunk of dead-*feature* code and the only item that changes product surface.

**Never touch:**
7. The 7 Category-C rule functions (§4) — dynamic registry/decorator dispatch; privatizing them churns ~30 test imports for zero benefit and risks masking the dispatch.
8. The 3 Category-D `run_batch` result types — legitimate public return surface.

### Cross-cutting interactions
- `privatize --include-heuristic` staged a transaction into `.semantic-tool/transactions/` this session; it is gitignored and unapplied — safe to ignore or delete the stray `*.jsonl`.
- Any privatization that rewrites `__all__` will be validated by pypeeker's own `barrel-only` / `unused-public-symbol` self-lint; run it after each edit.
- After Tier 1–2, re-check `architecture.md:53,140` and `CLAUDE.md:58` — they document `Capability` as reserved; those notes become stale once the enum is deleted (tracked separately as review doc 05 per `review/00-overview.md:83`).
