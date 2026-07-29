# 03 — Test Coupling: What Breaks If I Refactor X

**Scope:** the test suite under `tests/` (72 `.py` files, ~1362 `def test_` functions,
95% coverage). This document maps *structural coupling* between the tests and the
production code so that before any refactor you can predict the blast radius. It is the
reference companion to `review/02-cli-output-contract.md` (which covers the CLI side); this
doc focuses on the **test side**.

Four coupling surfaces, in descending order of blast radius:

1. Tests import and assert on **private (`_`-prefixed) helpers** → behavior-preserving renames break tests.
2. Tests hard-code **symbol-ID string literals** (~1039 occurrences across 54 files) → changing the ID grammar has suite-wide reach.
3. Tests `json.loads(result.output)` and assert on **specific CLI JSON keys** → schema changes break CLI tests.
4. **Test-isolation fragilities**: raw `os.chdir` without restore, a silent-skip self-test, process-cwd reliance.

---

## 1. Private-helper imports (renaming these breaks tests)

Every test below reaches *past* a package barrel to import an underscore-prefixed name
directly from a submodule. A behavior-preserving rename of any of these production symbols
is a **test-breaking change** even though nothing about behavior changed. All are aliased
on import (`_foo as foo`) — the alias hides that a private contract is being consumed.

| Test file:line | Private symbol imported | Defining module:line | What the test asserts on it |
|---|---|---|---|
| `test_rule_naming_conventions.py:14` | `_naming_conventions` (as `naming_conventions`) | `check/builtin/naming_conventions.py:236` | Calls the rule fn directly, asserts on returned `Violation`s |
| `test_rule_naming_conventions.py:15` | `_rename_pair` (as `rename_pair`) | `check/builtin/naming_conventions.py:155` | Asserts `(old, new)` rename pair derived from a violation |
| `test_rule_naming_conventions.py:16` | `_to_pascal_case` (as `to_pascal_case`) | `check/builtin/naming_conventions.py` (near `_to_snake_case`) | Pure string-transform assertions |
| `test_rule_naming_conventions.py:17` | `_to_snake_case` (as `to_snake_case`) | `check/builtin/naming_conventions.py:84` | Pure string-transform assertions (snake-casing edge cases) |
| `test_planner.py:5` | `_position_to_byte_offset` (as `position_to_byte_offset`) | `refactor/planner.py:599` | Byte-offset math from (line, col) against content bytes |
| `test_batch.py:39` | `_rebind` (as `rebind`) | `refactor/simulate.py:64` | Simulated rebind of a symbol during batch planning |
| `test_overlay_store.py:17` | `_rebind` (as `rebind`) | `refactor/simulate.py:64` | Same helper, second consumer |
| `test_builtin_discovery.py:8` | `_import_submodules` (as `import_submodules`) | `check/builtin/__init__.py:26` | Rule-package auto-discovery returns expected submodule names |
| `test_resolve.py:9` | `_ResolutionKind` (as `ResolutionKind`) | `resolve.py:29` (enum) | Asserts resolution classification (DIRECT/BARREL/RECEIVER_*/IMPORT_ALIAS) |
| `test_check_fix.py:20` | `_unused_imports` (as `unused_imports`) | `check/builtin/unused_imports.py:47` | Calls rule fn directly for fix-protocol assertions |
| `test_privatize_cli.py:41` | `_PRIVATIZE_RULES` | `cli.py:884` (module constant tuple) | Asserts the set of rule names offered by `privatize --rule` |
| `test_rule_born_private.py:24` | `_born_private` (as `born_private`) | `check/builtin/born_private.py:98` | Calls the project-scope rule fn directly |
| `test_treebuild.py:10` | `_reconcile_tree` (as `reconcile_tree`) | `treebuild.py:128` | Incremental cross-file symbol-tree reconciliation |
| `test_models.py:3` | `_Capability` (as `Capability`) | `models/capabilities.py:6` (reserved roadmap enum) | Enum membership / serialization; enum has *no production consumers* |

**Not a private-helper coupling (noted to avoid confusion):**
`test_purity_typed_receivers.py:17` does `from pypeeker.resolve import bare_type_name as
_bare_type_name` — this is a **public** function (`resolve.py:65`) aliased *to* a private
name inside the test. Renaming `bare_type_name` still breaks it, but it is not "test reaches
into private production API"; it is ordinary public-API coupling.

### Character of each coupling

- **Pure helpers safe to keep testing** (deterministic, no I/O, cheap to test in isolation):
  `_to_snake_case`, `_to_pascal_case`, `_rename_pair`, `_position_to_byte_offset`. These are
  genuinely worth unit-testing directly; the friction is only the leading underscore.
- **Rule-function-as-unit** couplings (`_naming_conventions`, `_unused_imports`,
  `_born_private`): the test bypasses the `check` engine and calls the rule body directly.
  This couples the test to the rule's *internal* callable signature, not just its registered
  behavior. These are the ones most worth reconsidering (see §5a).
- **Framework internals** (`_import_submodules`, `_reconcile_tree`, `_rebind`,
  `_ResolutionKind`): white-box tests of mechanics. `_ResolutionKind` in particular pins an
  enum's *member names*.
- **`_PRIVATIZE_RULES`** at `cli.py:884`: a module-level tuple. Any reordering/renaming of
  privatize rule identifiers breaks `test_privatize_cli.py`.
- **`_Capability`**: per CLAUDE.md this is a reserved roadmap enum with no current consumers,
  yet `test_models.py` keeps it exercised. Removing/renaming it is a pure test break with no
  production impact — a trap.

---

## 2. Symbol-ID string literals (blast radius of a grammar change)

The symbol-ID grammar is owned in one place — `models/symbol_id.py` — and documented there as
`module.path:Scope.Chain:local$N`, with sentinel prefixes `<builtins>.` and `<unresolved>.`.
Tests, however, **hand-type** these IDs as bare string literals everywhere.

### Quantification

- **~1039 symbol-ID-shaped string literals** across **54 of 72 test files**.
  (Heuristic: quoted, no whitespace, containing `:` joining identifier-ish parts. A handful
  may be incidental `"a:b"` strings, but the domain makes nearly all of them real IDs.)
- Distinct most-common literals: `"mod:f"` (72×), `"test:foo"` (41×), `"lib:helper"` (41×),
  `"m:Foo"` (33×), `"mod:helper"` (22×), `"m:x"` (21×), `"mod:f:x"` (18×), `"lib:Svc.run"` (18×).
- Any change to the `:` / `.` separators, the module-path prefix, or the leaf-attachment rule
  would require touching **more than half the suite**.

### Highest-concentration files (edit these first if the grammar moves)

| Rank | File | Symbol-ID literals |
|---|---|---|
| 1 | `test_intents.py` | 180 |
| 2 | `test_binder.py` | 104 |
| 3 | `test_resolve.py` | 82 |
| 4 | `test_hierarchy.py` | 68 |
| 5 | `test_privatize.py` | 67 |
| 6 | `test_batch.py` | 55 |
| 7 | `test_planner.py` | 50 |
| 8 | `test_purity.py` | 36 |
| 9 | `test_convention_renames.py` | 35 |
| 10 | `test_analysis_facts.py` | 26 |

(Full per-file counts continue down a long tail; `test_symbol_id.py` (24) is the one file that
*should* own grammar assertions — see §5c.)

### Sub-grammar features that are separately pinned

| Grammar feature | Literal form in tests | Files touching it | Count |
|---|---|---|---|
| Builtin sentinel | `<builtins>.len` etc. | `test_binder.py`, `test_binder_builtins.py`, `test_binder_forward_refs.py`, `test_check_rules.py`, `test_symbol_id.py` | 20 occurrences |
| Unresolved sentinel | `<unresolved>.method` | `test_binder.py`, `test_binder_receiver_chain.py`, `test_binder_subscript_writes.py`, `test_check_rules.py`, `test_rule_unused_return_value.py`, `test_symbol_id.py` | 6 files |
| Shadow suffix | `x$2`, `$3` | `test_binder.py`, `test_intents.py`, `test_privatize.py`, `test_rule_star_imports.py`, `test_symbol_id.py` | 19 occurrences |

Changing any one of these three sentinels/suffixes is a smaller but still multi-file change,
and each is pinned in `test_symbol_id.py` plus several binder tests.

**Bottom line:** the symbol-ID grammar behaves as a *de facto frozen contract enforced by the
test suite*, but it is enforced by ~1039 scattered literals rather than by one owned constant
set. That is the worst of both worlds: high change cost, no single point of intentional review.

---

## 3. CLI-JSON-shape coupling (the test side)

16 test files call `json.loads(...)`; the ones that parse **command output**
(`json.loads(result.output)`) and assert on specific keys are the schema-coupled set. Keys
below are those the tests actually index into — renaming/removing any key breaks the file.

| Test file | Command(s) exercised | JSON keys asserted |
|---|---|---|
| `test_cli.py` | `index`, `symbol`, `refs`, `refs --all`, `scope` | `indexed`, `skipped`, `name`, `location`, `scope`, `in_scope_id`, `file_path`, `resolution` |
| `test_cli_freshness.py` | `symbol`, `refs`, `check`, `plan-extract-variable`, `plan-inline-variable`, `plan-extract-method` | `error` (contains `"stale"`), `name`, `operation`, `new_name`; empty-list `== []` results |
| `test_rename_cli.py` | `plan-rename`, `apply` | `tx_id`, `status`, `error`, `operation`, `old_name`, `new_name`, `files_modified`, `edit_count` |
| `test_purity_cli.py` | `purity` | `observations`, `symbol_id`, `pure`, `kind`, `reason`, `name`, `line`, `callee` |
| `test_transactions_cli.py` | `transactions list/show/apply/cancel/rollback`, `plan-rename` | `tx_id`, `status`, `error`, `header`, `edits`, `operation`, `old_name`/`old`, `new_name`/`new`, `edit_count`, `files_restored`, `files_reindexed`, `files_reindex_failed`, `files_affected`, `file_rename`, `file`; empty-list `== []` |
| `test_plan_batch_cli.py` | `plan-batch`, `apply`, `rollback` | `tx_id`, `error`, `dropped`, `id`, `status`, `reason`, `executed`, `src`, `kind`, `files_affected`, `edit_count`, `detail` |
| `test_privatize_cli.py` | `privatize`, `transactions show` | `tx_id`, `symbol_id`, `skipped`, `status`, `header`, `executed`, `edit_count`, `src`, `reason`, `operation`, `files_affected`, `edits`, `dropped`, `applied` |
| `test_promote_demote.py` | `promote`, `demote`, `apply`, `transactions list/rollback` | `code`, `tx_id`, `operation`, `error`, `warnings`, `status`, `old_name`, `new_name`, `files_reindex_failed`, `files_affected`, `demote` |
| `test_purity_cli.py` / `test_confidence.py` | `check` variants | `violations` (list of identity dicts) |
| `test_check_fix.py` | `check`, `transactions show` | full-report `report` dicts; baseline files: `{"symbols": [...]}`, `{"violations": {...}}` |
| `test_rule_docstring_drift.py`, `test_rule_star_imports.py` | `check`, `apply` | `report`, `status` (`== "applied"`) |
| `test_baseline.py`, `test_rule_born_private.py` | baseline file I/O (not command output) | `{"symbols": [...]}` shape of the on-disk baseline JSON |
| `test_transaction_storage.py` | on-disk transaction log lines | `status` (`"failed"`/`"applied"`), header/edit line shapes |

Notes for refactoring:

- **Envelope keys** appear across nearly every command test: `tx_id`, `status`, `operation`,
  `error`, `edit_count`, `files_affected`. These are the highest-value-to-freeze keys.
- Several tests assert **exact empty results** (`json.loads(result.output) == []`) — adding a
  wrapper object around list results (e.g. `{"transactions": []}`) would break
  `test_cli_freshness.py`, `test_transactions_cli.py`, `test_cli.py`.
- Baseline-file JSON (`{"symbols": [...]}`, `{"violations": {...}}`) is a *persisted* schema
  pinned by `test_check_fix.py`, `test_baseline.py`, `test_rule_born_private.py`; changing it
  also invalidates on-disk baselines in real projects, so it is doubly load-bearing.

---

## 4. Test-isolation fragilities

### 4a. `os.chdir` without restore (process-cwd leakage)

There are **two chdir populations**:

- **Safe — auto-restored** via pytest's `monkeypatch.chdir` (undone at test teardown):
  `test_check_engine.py`, `test_plan_batch_cli.py`, `test_privatize_cli.py`,
  `test_promote_demote.py`.
- **Leaky — raw `os.chdir(...)`, never restored** (no `getcwd()` save anywhere in the suite —
  confirmed: zero `os.getcwd()`-for-restore sites):
  `test_baseline.py:202`, `test_check_fix.py:67`, `test_cli.py` (5 sites: lines 64,76,97,130,164),
  `test_cli_freshness.py:26,107`, `test_confidence.py:181`, `test_purity_cli.py` (5 sites),
  `test_rename_cli.py` (6 sites), `test_rule_docstring_drift.py:387`,
  `test_rule_star_imports.py:335`, `test_transactions_cli.py` (12 sites).

The leaky population changes the **process working directory** and leaves it changed. Because
`tmp_path` dirs are unique per test the immediate test still passes, but the *next* test that
relies on cwd inherits a stale, often-deleted directory.

**This pollution is real and already worked around defensively** — the smoking gun is three
production-code-adjacent test comments that anchor to a fixed path specifically to survive it:

- `test_check_rules.py:988` — *"Anchor to the repo root (not cwd) so a chdir'd sibling test can't…"*
- `test_rule_import_time_side_effects.py:211` — *"…(not cwd) because other test modules chdir without restoring."*
- `test_rule_unused_return_value.py:334` — *"Anchor to this file, not cwd — earlier tests may chdir."*

Consequence for refactoring: **test-ordering is load-bearing.** Any change that reorders test
collection (parallelization via `pytest-xdist`, renaming files, adding a test earlier in the
alphabet) can flip a green suite red for reasons unrelated to the change. This also means the
suite is *not* currently safe to run under `-p xdist` in a way that shares cwd.

### 4b. `test_purity_self.py` silent skip → green CI on a clean checkout

`test_purity_self.py` is described in-file as *"The killer regression test: behavior on real,
non-trivial Python code."* Its `project_store` fixture (`scope="module"`) is:

```python
INDEX_DIR = REPO_ROOT / ".semantic-tool" / "index" / "src" / "pypeeker"

@pytest.fixture(scope="module")
def project_store():
    if not INDEX_DIR.exists():
        pytest.skip(f"No project index at {INDEX_DIR}; run `pypeeker index src/` first.")
    return IndexStore(REPO_ROOT)
```

`.semantic-tool/` is **gitignored and regenerated locally** (per CLAUDE.md). So on a fresh
checkout — including a clean CI runner — `INDEX_DIR` does not exist and **all 10 tests in the
file skip silently.** The suite reports green while the "killer regression test" ran *zero*
assertions. It only actually runs if `pypeeker index src` was executed first in the same
workspace (which the documented self-lint step does — but only if CI is wired to run
`index` *before* pytest). Since CI ships only as `.github/ci.yml.example` (not yet active),
nothing currently guarantees the index exists at pytest time.

The parametrized IDs inside this file (e.g.
`"pypeeker.storage.index_store:IndexStore.save"`,
`"pypeeker.refactor.applier:TransactionApplier._apply_file_rename"`) also hard-code both the
symbol-ID grammar **and real production module paths / method names** — so this file couples
to §2's grammar *and* to production internals; renaming `TransactionApplier._reindex_files`
breaks it (when it runs).

### 4c. Shared / global state and ordering assumptions

- **`check/builtin` self-registration by import side-effect.** Rules register via a module
  import in `check/engine.py`; `test_builtin_discovery.py` asserts on `_import_submodules`.
  This is process-global registry state — fine within a run, but any test that imports a rule
  module mutates a shared registry.
- **`clear_symbol_baseline` / baseline files.** `test_check_fix.py:19` imports
  `clear_symbol_baseline`; baseline tests write and read `.semantic-tool` baseline JSON.
  Tests that don't clear between cases rely on tmp_path isolation, not explicit teardown.
- **No autouse fixtures.** `conftest.py` defines only opt-in fixtures (`adapter`, `store`,
  `indexed_project`, `bind_source`, …); there is **no autouse cwd-guard**, which is exactly
  why the §4a leakage is possible. Adding one autouse `chdir`-restoring fixture in
  `conftest.py` would neutralize the entire §4a class at a stroke.

---

## 5. Recommendations (prioritized by value / effort)

### 5a. Private helpers: keep test-visible vs rewrite against the barrel

| Helper | Recommendation | Rationale |
|---|---|---|
| `_to_snake_case`, `_to_pascal_case`, `_rename_pair`, `_position_to_byte_offset` | **Keep unit-tested; promote to non-underscore or add to an internal `__all__` for the package's own test surface** | Pure, deterministic, genuinely worth isolated tests. The only problem is the fig-leaf alias. Either drop the underscore (they are effectively package-public) or accept them as a declared "test-visible internals" set. Low effort, high clarity. |
| `_naming_conventions`, `_unused_imports`, `_born_private` (rule fns) | **Rewrite tests to drive the `check` engine / registry (`get_rule`, `get_project_rule`) instead of calling the private fn** | These tests already import `get_rule`/`get_project_rule` alongside the private fn — the public path exists. Testing through the engine both frees the rule body to be refactored and exercises registration+context wiring the direct call skips. Medium effort, high value (unblocks rule-internal refactors). |
| `_ResolutionKind` | **Keep, but assert via the public classification surface if one exists**; otherwise treat the enum member names as a mini frozen contract | Enum-member coupling is brittle; but resolution kinds are a real semantic vocabulary. Low priority. |
| `_import_submodules`, `_reconcile_tree`, `_rebind` | **Leave as white-box tests but document them as internal-contract tests** | Genuinely testing mechanics with no public equivalent. Accept the coupling explicitly rather than hiding it behind aliases. |
| `_PRIVATIZE_RULES` | **Expose the privatize rule set through an `app/`-layer accessor and assert on that** | A CLI module constant is the wrong seam; the rule set is application config. Low effort. |
| `_Capability` (`test_models.py`) | **Delete the test or gate it behind "reserved, no consumers"** | Per CLAUDE.md the enum has no production consumers; the test creates pure friction (blocks removing dead code). Zero-value coupling — highest ratio to fix. |

### 5b. Declare FROZEN contracts

**Yes — declare both the symbol-ID grammar and the CLI JSON envelope as frozen contracts, in
writing, in the docs that CLAUDE.md already points to.** Rationale:

- The symbol-ID grammar is *already* frozen in practice — ~1039 literals across 54 files make
  any change a suite-wide event. The choice is not "frozen vs flexible" but "frozen by
  accident vs frozen by intent." `models/symbol_id.py` is already the single owner of
  construction/parsing; the missing piece is a stated stability guarantee plus a test-side
  helper (5c) so the freeze is enforced in *one* reviewed place instead of 1039 literals.
  These IDs are also user-facing (they appear in CLI JSON and on disk under `.semantic-tool/`),
  so breaking them breaks persisted indexes and any LLM/human driving the CLI — a real
  external contract.
- The CLI JSON envelope keys (`tx_id`, `status`, `operation`, `error`, `edit_count`,
  `files_affected`, plus the empty-list-as-`[]` convention) are the machine interface the tool
  exists to provide. Freeze the envelope; allow *additive* fields only. Cross-reference
  `02-cli-output-contract.md` for the producer side.
- Baseline-file JSON (`{"symbols": [...]}`) is a *persisted* schema and should be frozen or
  versioned explicitly, since changing it silently invalidates real users' baselines.

### 5c. Lower-risk testing patterns (concrete)

1. **A symbol-ID builder used by tests instead of hand-typed literals.** Add a test helper
   (e.g. `sid(module, *scopes, local=None, shadow=None)`) that constructs IDs through
   `models/symbol_id.py`'s own construction functions. Then a grammar change touches the
   builder + `test_symbol_id.py` only, not 54 files. Migrate highest-concentration files first
   (`test_intents.py` 180, `test_binder.py` 104, `test_resolve.py` 82). High value; effort is
   incremental (can be done file-by-file).
2. **Make `test_symbol_id.py` the single grammar-assertion owner** and have other tests assert
   *equality against the builder's output*, never against raw strings. This turns §2 from a
   scattered contract into an owned one.
3. **Route CLI-schema tests through a small response-shape helper** (e.g.
   `assert_envelope(output, tx_id=..., status=...)`) so key renames are a one-line change.
   Lower value than the symbol-ID builder but cheap.
4. **Add one autouse cwd-guard fixture in `conftest.py`** that records `os.getcwd()` and
   restores it on teardown. This neutralizes the entire §4a leakage class and removes the need
   for the three defensive "anchor, not cwd" workarounds. **Highest value-to-effort item in
   this document** — a few lines, removes an ordering-dependence that blocks parallelization.
5. **Make `test_purity_self.py` fail loudly instead of skipping**, or auto-build the index in a
   `scope="session"` fixture. Options: (a) a session fixture that runs `pypeeker index src`
   when `INDEX_DIR` is missing; (b) `pytest.fail` instead of `pytest.skip` when CI is detected
   (`if os.environ.get("CI")`). Otherwise activate CI (`ci.yml.example`) with `index` ordered
   *before* pytest and document that pytest-without-index skips the regression net.

### Priority order (value / effort)

1. **Autouse cwd-guard fixture** (§5c-4) — tiny, removes ordering fragility + 3 workarounds.
2. **Delete/gate the `_Capability` test** (§5a) — zero-value coupling, unblocks dead-code removal.
3. **Symbol-ID builder helper + migrate top-5 files** (§5c-1/2) — biggest structural blast-radius reduction.
4. **Fix `test_purity_self.py` silent skip** (§4b/§5c-5) — restores a regression net CI currently loses.
5. **Route rule tests through the engine** (§5a rule fns) — unblocks rule-internal refactors.
6. **Write down the FROZEN contracts** (§5b) — cheap, makes all the above intentional.
7. **CLI response-shape helper** (§5c-3) — nice-to-have once the schema is declared frozen.
