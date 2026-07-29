# 02 — CLI Output Contract Audit & Standardization Proposal

Scope: `src/pypeeker/cli.py` (21 commands across the `main` group plus the
`transactions` sub-group). Every command prints JSON to stdout via
`click.echo(json.dumps(...))` — **except `check`**, which prints ruff-style text
lines. This document (1) inventories the exact success and error JSON shape of
each command with `file:line` citations and `to_dict` sources, (2) catalogs the
distinct error-envelope variants and the inconsistencies between them, (3) notes
the `check` text-vs-JSON divergence, (4) proposes a standardized envelope plus a
shared emit/error helper, (5) measures the test blast radius per command, and
(6) recommends a migration path.

All JSON dict success payloads flow through one of two serializers:

- `pypeeker.models.to_dict` — the recursive dataclass→plain-data serializer at
  `src/pypeeker/models/serialize.py:41` (dataclass→dict, `Enum`→`.value`,
  list/tuple→list, dict→dict, scalars pass through). Used for `Symbol`,
  `Reference`, `TransactionSummary`, `TransactionHeader`, `EditEntry`,
  `FileRenameEntry`, scope nodes, and purity observations.
- Hand-built `dict` literals inside `cli.py` (index result, apply/rollback
  result, purity verdict, plan-batch/privatize/check-fix reports) — these are
  **not** dataclasses and are assembled inline.

There is **no shared emit helper and no shared error helper** anywhere in
`cli.py`. Every JSON write is an independent `click.echo(json.dumps(...))` call;
every error is a hand-rolled dict literal.

---

## 1. Per-command output contract (big table)

Legend for "Top-level type": **dict** = JSON object, **array** = JSON list.
"Success builder" cites where the emitted structure is assembled.

| Command | Success top-level type | Success shape (keys → types) | Success builder (`file:line`) + `to_dict` source | Error shape(s) | Error site (`file:line`) | Exit (err) |
|---|---|---|---|---|---|---|
| `index` | dict | `{indexed:[str], skipped:[str], errors:[dict], removed:[str]}` | `cli.py:87` → `result.to_dict()` at `indexer.py:45` (`_IndexResult`, hand-built dict) | `{"error": str}` (`"Path not found: …"`) | `cli.py:80` | 1 |
| `check` | **text (not JSON)** | ruff-style `path:line: [rule] message` lines + summary line(s); prints nothing structured | `cli.py:296-312` (`str(v)` per violation) | none as JSON — violations ARE the output; exits 1 when any shown | n/a (violations to stdout) | 1 |
| `check --fix` | dict | `{applied:[...], skipped_conflicts:[...], declined:[...], residual_violations:int, tx_id:str\|null}` | `cli.py:139-150` (hand-built dict; `applied`/`skipped_conflicts`/`declined` come from `apply_check_fixes` outcome) | `{"error": str, "tx_id": str}` | `cli.py:135` | 1 |
| `symbol` | **array** | `[Symbol, …]` | `cli.py:329-330` → `[to_dict(s) …]` (`Symbol` dataclass via `serialize.to_dict`) | — (empty array `[]` on no match, exit 0) | n/a | — |
| `refs` | **array** | `[Reference, …]`; with `--all` each item gains `"resolution": str` | `cli.py:374-379` → `to_dict(r)` / `{**to_dict(r.reference), "resolution": r.via.value}` | — (empty array on no match, exit 0) | n/a | — |
| `tree` | **array** | `[TreeNode, …]` (root nodes) or member dicts | `cli.py:399-401` → `to_dict(node)` / `engine.members(id)` | — (empty array, exit 0) | n/a | — |
| `purity` | dict | `{symbol_id:str, pure:bool, observations:[{kind:str, …obs fields}]}` | `cli.py:456-465` (hand-built; each obs `{"kind": type.__name__, **to_dict(obs)}`) | (a) `{error:str, reason:str, symbol_id:str, detail:str\|null}`; (b) `{error:str, reason:"not_found_or_not_a_function"}` (pragma no-cover) | (a) `cli.py:428-436`; (b) `cli.py:444-450` | 1 |
| `scope` | dict | `{scope:Scope, visible_symbols:[Symbol], scope_chain:[Scope]}` | `cli.py:706` → `engine.get_scope_at()` at `query/engine.py:179-183` (hand-built dict of `to_dict`) | (a) `{"error": str}` bad format; (b) `{"error": str}` bad line; **(c)** `{"error": str}` from engine (file not indexed / no scope) emitted with **exit 0** | (a) `cli.py:695`; (b) `cli.py:702`; (c) `query/engine.py:168,172` echoed at `cli.py:706` | (a)(b) 1; **(c) 0** |
| `plan-rename` | dict | `TransactionSummary` (see below) | `cli.py:779` → `to_dict(summary)` (`TransactionSummary` at `models/transaction.py:77`) | `{"error": str}` | `cli.py:781` | 1 |
| `plan-extract-variable` | dict | `TransactionSummary` | `cli.py:504` → `to_dict(summary)` | `{"error": str}` (`ExtractVariableError`/`ValueError`) | `cli.py:502` | 1 |
| `plan-extract-method` | dict | `TransactionSummary` | `cli.py:562` → `to_dict(summary)` | `{"error": str}` (`ExtractMethodError`) | `cli.py:560` | 1 |
| `plan-inline-variable` | dict | `TransactionSummary` | `cli.py:528` → `to_dict(summary)` | `{"error": str}` (`InlineVariableError`) | `cli.py:526` | 1 |
| `plan-batch` | dict | `{tx_id:str\|null, executed:[{id,kind}], dropped:[{id,reason,detail}], files_affected:[str], edit_count:int}` | `cli.py:663-677` (hand-built dict) | (a) `{"error": str}` (read/JSON/build/no-intents/schedule/flatten); (b) `{"error": str, "dropped":[{id,reason,detail}]}` (abort / all-dropped) | (a) `cli.py:632,634,638,640,652`; (b) `cli.py:650,658` | 1 |
| `scope` (dup — see above) | | | | | | |
| `demote` | dict | `TransactionSummary` + optional `"warnings":[str]` | `cli.py:826-829` → `to_dict(result.summary)`, then `output["warnings"]=…` | `{"error": str, "code": str}` (`VisibilityOpError.code`) | `cli.py:824` | 1 |
| `promote` | dict | `TransactionSummary` + optional `"warnings":[str]` | `cli.py:875-878` → `to_dict(result.summary)` + warnings | `{"error": str, "code": str}` | `cli.py:873` | 1 |
| `privatize` | dict | `{tx_id, executed:[{id,symbol_id,new_name}], dropped:[{id,reason,detail}], skipped:[{symbol_id,reason,detail}], warnings:[str], files_affected:[str], edit_count:int}` (+ `"applied":{…}` when `--apply`) | `cli.py:970-997` (hand-built dict) | **`{…full success dict…, "error": str}`** — `error` grafted onto the success payload when `--apply` fails | `cli.py:993` | 1 (also 1 when `summary is None`, `cli.py:990`, with NO error key) |
| `apply` | dict | `{tx_id:str, status:"applied", files_modified:[str], files_reindexed:[str], files_reindex_failed:[…]}` | `cli.py:1017` → `applier.apply()` at `refactor/applier.py:138-144` (hand-built dict) | `{"error": str}` (`ApplyError`) | `cli.py:1019` | 1 |
| `rollback` | dict | `{tx_id:str, status:"rolled_back", files_restored:[str], files_reindexed:[str], files_reindex_failed:[…]}` | `cli.py:1043` → `applier.rollback()` at `refactor/applier.py:255-261` | `{"error": str}` (`RollbackError`) | `cli.py:1045` | 1 |
| `transactions list` | **array** | `[{tx_id, operation, status, created_at, edit_count, files_affected}, …]` | `cli.py:1075-1085` (hand-built dict per tx) | — (empty array, exit 0) | n/a | — |
| `transactions show` | dict | `{header:TransactionHeader, edits:[EditEntry], file_rename:FileRenameEntry\|null}` | `cli.py:1102-1107` → `to_dict(...)` on each | `{"error": str}` (`"Transaction not found: …"`) | `cli.py:1099` | 1 |
| `transactions cancel` | dict | `{tx_id:str, status:"cancelled"}` | `cli.py:1139` (hand-built dict) | (a) `{"error": str}` not found; (b) `{"error": str}` not pending | (a) `cli.py:1123`; (b) `cli.py:1127-1136` | 1 |

### `TransactionSummary` (shared success payload)

Emitted by `plan-rename`, `plan-extract-variable`, `plan-extract-method`,
`plan-inline-variable`, `demote`, `promote`. Definition at
`src/pypeeker/models/transaction.py:77-88`, serialized by `to_dict`:

```
{ tx_id:str, operation:str, symbol_id:str, old_name:str, new_name:str,
  edit_count:int, created_at:str, files_affected:[str] }
```

`demote`/`promote` conditionally append `"warnings":[str]` (only when non-empty
— so a key that is *sometimes absent*, `cli.py:827-828 / 877-878`).

### Cross-cutting shape observations

- **Top-level type is not uniform.** Four commands return a bare JSON **array**
  (`symbol`, `refs`, `tree`, `transactions list`); the rest return an object.
  An LLM consumer cannot assume `response.error` exists without first testing
  the top-level type.
- **`indent` is not uniform.** Most calls pass `indent=2`; several error/edge
  calls omit it (`index` error `cli.py:80`, `check --fix` error `cli.py:135`,
  the plan-* error echoes `cli.py:502,526,560,781`, `scope` errors `cli.py:695,702`,
  `apply`/`rollback` errors `cli.py:1019,1045`, `transactions show/cancel`
  errors `cli.py:1099,1123,1127`, purity fallback `cli.py:446`). Purely
  cosmetic, but it confirms these are copy-pasted one-offs, not a helper.
- **`status` string is a bare enum-ish literal**, spelled differently per
  command: `"applied"`, `"rolled_back"`, `"cancelled"`, `"pending"`. No shared
  constant.

---

## 2. Error-envelope variants catalog

Every error site is a hand-rolled dict literal. There are **8 structurally
distinct error envelopes** across ~18 emit sites.

| # | Envelope shape | Meaning of extra keys | Sites (`file:line`) |
|---|---|---|---|
| V1 | `{"error": str}` | none — bare message | `index` 80; `plan-extract-variable` 502; `plan-inline-variable` 526; `plan-extract-method` 560; `plan-rename` 781; `apply` 1019; `rollback` 1045; `transactions show` 1099; `transactions cancel` 1123 & 1127; `scope` 695 & 702; `plan-batch` 632,634,638,640,652 |
| V2 | `{"error": str, "tx_id": str}` | `tx_id` of the partially-applied fix transaction | `check --fix` 135 (`CheckFixApplyError.tx_id`) |
| V3 | `{"error": str, "code": str}` | `code` = stable machine refusal class | `demote` 824; `promote` 873 (`VisibilityOpError.code`) |
| V4 | `{"error": str, "dropped": [{id,reason,detail}]}` | per-intent drop records | `plan-batch` 650 (abort), 658 (all dropped) |
| V5 | `{"error": str, "reason": str, "symbol_id": str, "detail": str\|null}` | structured analysis failure | `purity` 428-436 (`ContextError`, reasons `not_found`/`not_a_function`, `context.py:130-133`) |
| V6 | `{"error": str, "reason": "not_found_or_not_a_function"}` | pared-down analysis failure | `purity` 444-450 (pragma no-cover fallback) |
| V7 | `{…full success dict…, "error": str}` | error grafted onto an otherwise-success payload | `privatize` 993 |
| V8 | `{"error": str}` **emitted with exit code 0** | engine-level "not found" that never reaches the CLI's exit path | `scope` via `query/engine.py:168,172` echoed at `cli.py:706` |

### Precise inconsistencies

1. **Key name for the machine code differs by command.** `demote`/`promote` use
   `"code"` (V3); `purity` uses `"reason"` (V5/V6). Same concept ("why did this
   fail, machine-readably"), two key names. `plan-batch` drop records use
   `"reason"` for a *third* meaning (per-intent drop cause).
2. **The `"error"` value is always a human string, never structured.** A
   consumer can only regex the prose. Tests already do this: `"not found" in
   output["error"]` (`test_rename_cli.py:134`), `"has been modified" in
   refused["error"]` (`test_plan_batch_cli.py:167`), `"stale" in …["error"]`
   (`test_cli_freshness.py:168`). That is a brittle contract baked into tests.
3. **V7 is genuinely ambiguous.** `privatize --apply` on apply failure emits the
   *entire success shape* (with a real `tx_id`, `executed`, etc.) **plus** an
   `"error"` key. A naive `if "error" in resp` check would treat a
   partially-successful privatize as a total failure and discard the plan data.
4. **V8 breaks the exit-code contract.** `scope` on an un-indexed file or a line
   with no scope returns `{"error": …}` but the CLI echoes `result` unguarded
   (`cli.py:705-706`) with **exit 0**. Every other error path exits 1. An LLM
   keying off exit code alone would mistake this error for success. (The two
   *format*-level scope errors, `cli.py:695/702`, correctly exit 1 — so `scope`
   is internally inconsistent with itself.)
5. **`privatize` with `summary is None` exits 1 with NO error key at all**
   (`cli.py:988-990`) — the failure is signalled only by `tx_id: null` + exit
   code. So "failure" spans three shapes within one command: no-error-key
   (nothing plannable), grafted-error (apply failed), and success.
6. **Exit codes for user error are mixed.** Rule/precondition failures exit 1;
   Click `UsageError` (e.g. `--baseline` + `--update-baseline`, `cli.py:249`;
   `--fix` + baseline, `cli.py:253`) and bad `--rule`/`--policy` `Choice` values
   exit **2** (`test_privatize_cli.py:258`); `scope` V8 exits 0. No single
   "invalid input" code.

---

## 3. `check` diverges: text, not JSON

`check` is the **only** command that does not emit JSON on its primary path. It
prints ruff/mypy-style lines `path:line: [rule] message` (`str(v)` per violation,
`cli.py:297,308`) followed by human summary lines (`cli.py:298-301` baseline
counts; `cli.py:284-287` `--update-baseline`; the hidden-count note
`cli.py:107-112`). Exit 1 when any shown violation remains.

This looks **intentional**: the command docstring explicitly states "Output
format matches ruff/mypy" (`cli.py:217-219`), and the design is that `check`
output drops into the same tooling/editor problem-matchers that consume
ruff. Tests assert only on exit codes and substring presence, never on JSON
(`test_check_engine.py:76-93`, `test_cli_freshness.py:96-100`).

**Notable exception:** `check --fix` *does* emit JSON (V2 error / the report dict
at `cli.py:139-150`), because it reports a transaction, not a lint result. So
`check` already speaks JSON on one sub-path — the text/JSON split is by mode, not
by command.

Observation only (no decision taken): a `check --json` mode emitting
`[{path,line,rule,message,confidence}, …]` would let LLM consumers parse
findings structurally instead of re-parsing the ruff line format, and would make
`check` consistent with the rest of the CLI. It should remain **opt-in** so the
default ruff-compatible stream is preserved. The confidence tier (currently a
trailing `[tier]` marker, `cli.py:222-223`) and the hidden-count summary would
need first-class fields in that mode.

---

## 4. Proposed standardized envelope + shared helpers

### 4.1 Design goals

- One predictable top-level type an LLM can branch on without a type sniff.
- Errors carry a **stable machine code** in a single, always-present key, plus a
  human message, plus arbitrary structured extras.
- Success payloads keep their current field names/shapes (see §6 — churn
  control).
- Exit code always agrees with the envelope (`ok=false` ⇒ non-zero).

### 4.2 Envelope

Adopt a top-level discriminator. Two viable forms:

**Option A — `ok` flag (recommended).**

```jsonc
// success
{ "ok": true,  "data": <existing payload, unchanged> }
// error
{ "ok": false, "error": { "code": "<stable-code>", "message": "<human>", ...extra } }
```

`data` holds the *current* success shape verbatim (including the array-returning
commands, whose array becomes `data: [...]`). This uniformly fixes the
"array-vs-object top level" problem: every response is now an object.

**Option B — error-only standardization (lighter).** Keep success payloads at
the top level exactly as today; only standardize the error branch to
`{"error": {"code","message", ...extra}}`. This is less churn but leaves the
array/object top-level split unfixed and keeps consumers unable to test one key
for success-vs-error.

Recommendation: **Option A** for a clean contract if a breaking bump is
acceptable; **Option B** if success payloads must stay byte-stable for existing
scripts (it is the smaller migration and still kills all 8 error variants). §6
recommends B-first, then A behind a flag.

### 4.3 Shared helpers (add to `cli.py`, near the top)

```python
def _emit(data: object, *, indent: int = 2) -> None:
    """Single success sink. Wraps in the standard envelope (Option A)."""
    click.echo(json.dumps({"ok": True, "data": data}, indent=indent, default=str))

def _emit_error(code: str, message: str, *, exit_code: int = 1, **extra: object) -> None:
    """Single error sink: stable code + message + structured extras, then exit."""
    body = {"code": code, "message": message, **extra}
    click.echo(json.dumps({"ok": False, "error": body}, indent=2, default=str))
    sys.exit(exit_code)
```

Every current `click.echo(json.dumps(...)); sys.exit(1)` pair collapses to one
`_emit_error(...)` call, and every success `click.echo(json.dumps(...))` to
`_emit(...)`. `default=str` (currently only on `scope`, `cli.py:706`) becomes
uniform, removing a latent serialization footgun.

A closed set of `code` values replaces the prose-only errors, e.g.:
`path-not-found`, `symbol-not-found`, `transaction-not-found`,
`transaction-not-pending`, `stale-index`, `invalid-location`,
`invalid-line`, `plan-refused`, `visibility-refused` (carrying the existing
`VisibilityOpError.code` as `extra["refusal"]` or reusing it as `code`),
`analysis-failed` (carrying `reason`), `batch-aborted` /
`all-intents-dropped` (carrying `dropped`), `apply-failed` (carrying `tx_id`),
`intents-unreadable` / `intents-invalid-json` / `intents-invalid`.

### 4.4 Before / after examples

**(a) `plan-rename` refusal — V1 today.**

```python
# before  (cli.py:780-782)
except RenamePlanError as e:
    click.echo(json.dumps({"error": str(e)}))
    sys.exit(1)
# after
except RenamePlanError as e:
    _emit_error("plan-refused", str(e))
```
Wire output: `{"ok": false, "error": {"code": "plan-refused", "message": "…"}}`.

**(b) `demote` refusal — V3 today (already has a code, but under `"code"`).**

```python
# before  (cli.py:823-825)
except VisibilityOpError as e:
    click.echo(json.dumps({"error": str(e), "code": e.code}))
    sys.exit(1)
# after
except VisibilityOpError as e:
    _emit_error("visibility-refused", str(e), refusal=e.code)
```
The machine code that tests depend on (`rename-refused`, `protected-public-api`,
`already-private`, `not-found`, `dunder`, `already-public`, `export-target`)
moves from `output["code"]` to `output["error"]["refusal"]` — a rename tests
must follow (see §5).

**(c) `privatize --apply` failure — V7 today (error grafted on success dict).**

```python
# before  (cli.py:991-996)
if apply_plan:
    if report.apply_error is not None:
        output["error"] = report.apply_error
        click.echo(json.dumps(output, indent=2))
        sys.exit(1)
    output["applied"] = report.applied
# after — the plan payload survives as structured context, not as a sibling of the report
if apply_plan:
    if report.apply_error is not None:
        _emit_error("apply-failed", report.apply_error, tx_id=output["tx_id"], plan=output)
        return
    output["applied"] = report.applied
_emit(output)
```
This removes the ambiguity of an object that is both a success and an error.

**(d) `scope` V8 exit-0 error — fixed for free.** Route the engine dict through
the CLI's error branch instead of echoing it raw:

```python
# before  (cli.py:705-706): echoes {"error": …} with exit 0
result = engine.get_scope_at(file_path, line)
click.echo(json.dumps(result, indent=2, default=str))
# after
result = engine.get_scope_at(file_path, line)
if "error" in result:
    _emit_error("scope-unavailable", result["error"])
_emit(result)
```

---

## 5. Test blast radius

17 files use `CliRunner`. The tests that assert on **output shape/keys** (not
just exit codes) are the ones that churn. Below, per command, the exact keys /
values tests bind to.

| Command | Test file:line — key/value asserted | Churn under Option A | Churn under Option B (errors only) |
|---|---|---|---|
| `index` | `test_cli.py:30,46,57,58` `output["indexed"]/["skipped"]` | wrap → `output["data"]["indexed"]` | none (success unchanged) |
| `symbol` | `test_cli.py:70`, `test_cli_freshness.py:76` `output[0]["name"]` | `output["data"][0]["name"]` | none |
| `refs` | `test_cli_freshness.py:45,61` (exit only) | none-ish | none |
| `tree` | `test_cli.py:103-104` (truthy) | `output["data"]` | none |
| `scope` | `test_cli.py:168,170` `output["scope"]["name"]` | `output["data"]["scope"]…` | none (but V8→exit-1 fix may flip an exit assertion if any test relied on exit 0 — none currently seen) |
| `purity` | `test_purity_cli.py:53,54,69,70,71,94,95,98` `output["pure"]/["observations"]`; **error**: `112-113,127-128` `"error" in output`, `output["reason"] in {not_found,not_a_function}` | success wrap; error `output["reason"]`→`output["error"]["reason"]`, and `"error" in output`→`output["ok"] is False` | error assertions rewritten (reason moves under `error`) |
| `plan-rename` | `test_rename_cli.py:59,63,91` `output["tx_id"]`; **error** `133-134,145-146` `"error" in output`, `"not found" in output["error"]` | success wrap; error message → `output["error"]["message"]` | error assertions rewritten |
| `plan-extract-variable` | `test_cli_freshness.py:151` `output["new_name"]`; **error** `168` `"stale" in …["error"]` | wrap; error → `error["message"]` | error rewritten |
| `plan-extract-method` | `test_cli_freshness.py:185` `"stale" in …["error"]` | error → `error["message"]` | error rewritten |
| `plan-inline-variable` | `test_cli_freshness.py:201` `output["operation"]` | `output["data"]["operation"]` | none |
| `plan-batch` | `test_plan_batch_cli.py:94,95,103,211,212` `output["dropped"]/["executed"]/["tx_id"]`; **error** `230,231,243,244,254,260,267,274,275,283,290` `"aborted" in output["error"]`, `output["error"]=="all intents were dropped"`, `output["dropped"][0]["reason"]`, substrings in `output["error"]` | success wrap; every error string assertion → `output["error"]["message"]`; `output["dropped"]` on abort moves under `error` extra | heavy error rewrite (11 asserts) |
| `demote` | `test_promote_demote.py:76-81,161` success fields + `warnings`; `test_cli.py:60` `"error" in output`; **error code** `141,150,168,177,183` `output["code"] == …` | success wrap; `output["code"]`→`output["error"]["refusal"]`; `"error" in output`→`output["ok"] is False` | `output["code"]`→`output["error"]["refusal"]`; `"error" in`→envelope |
| `promote` | `test_promote_demote.py:195-197,218-219` success; **error code** `268,276,286,300,301` `output["code"]` | as demote | as demote |
| `privatize` | `test_privatize_cli.py:126,130,150,156,166,180,191,221,222,271` `output["executed"]/["skipped"]/["tx_id"]/["applied"]`; **exact-dict** `309-317` `output == {tx_id,executed,dropped,skipped,warnings,files_affected,edit_count}` | success wrap breaks the **exact-dict equality** at 309; V7 apply-error path (`993`) has no direct test but shape changes | exact-dict at 309 is a *success* shape → unchanged under B; only V7 error path changes |
| `apply` | `test_rename_cli.py:69,118`, `test_extract_variable.py:49`, `test_check_fix.py:50`, `test_batch.py:637`, `test_plan_batch_cli.py:107` `output["status"]=="applied"`; **error** `test_plan_batch_cli.py:167` `"has been modified" in refused["error"]` | success wrap → `output["data"]["status"]`; error → `error["message"]` | error rewritten; success unchanged |
| `rollback` | `test_rename_cli.py:...`, `test_batch.py:642`, `test_transactions_cli.py:225,228` `output["status"]=="rolled_back"`; **error** `test_transactions_cli.py:245,263` `"not applied"/"modified" in output["error"]` | wrap; error → `error["message"]` | error rewritten |
| `transactions list` | `test_transactions_cli.py:94,96,116,125,201` `output[0]["status"]/["tx_id"]` | `output["data"][0]…` | none |
| `transactions show` | `test_transactions_cli.py:142,143`, `test_privatize_cli.py:180`, `test_check_fix.py:499` `output["header"]["status"]`; **error** `test_transactions_cli.py:163` `"not found" in output["error"]` | wrap; error → `error["message"]` | error rewritten |
| `transactions cancel` | `test_transactions_cli.py:178` `output == {"tx_id":…, "status":"cancelled"}` (**exact dict**); **error** `196,197,211` `"pending"/"applied"/"not found" in output["error"]` | success exact-dict breaks under wrap; errors → `error["message"]` | exact-dict success unchanged; errors rewritten |
| `check --fix` | `test_check_fix.py:479,485,486,487,489,552,553,555,570`, `test_rule_docstring_drift.py:401,404,405,422,423` `report["applied"]/["skipped_conflicts"]/["declined"]/["residual_violations"]/["tx_id"]`; **exact-dict** `test_check_fix.py:587-591` `result == {applied,skipped_conflicts,declined,residual_violations,tx_id}` | success wrap breaks exact-dict at 587; error V2 → `error["tx_id"]` | exact-dict is success → unchanged under B; V2 error path (no direct assert found) changes |
| `check` (text) | `test_check_engine.py:76-93`, `test_cli_freshness.py:96-100` exit codes + substrings | **none** (text path untouched) | none |

### Effort estimate

- **Option B (errors only):** ~10 test files touched, ~35 individual error
  assertions rewritten (mostly `output["error"]` → `output["error"]["message"]`,
  and `output["code"]` → `output["error"]["refusal"]` for promote/demote's ~10
  code asserts, and purity's `output["reason"]` → `output["error"]["reason"]`).
  The three **exact-dict success** assertions (`transactions cancel` 178,
  `privatize` 309, `check --fix` 587) are **success** payloads → untouched.
  Roughly **half a day**, mechanical, plus the `scope` V8 exit-code fix (1 new
  assertion). Source side: ~18 error sites → `_emit_error`, plus define the code
  set. Low risk.
- **Option A (full envelope):** adds success-side churn to **every** shape
  assertion — the four array commands and all `output["…"]` success reads gain a
  `["data"]` hop, and all three exact-dict equalities break. ~15 test files,
  ~120 assertions. Roughly **1.5–2 days**. Higher risk of missing a spot;
  benefits are a fully uniform contract.

---

## 6. Recommended migration

**Standardize errors first (Option B), keep success payloads byte-stable.**
Rationale:

1. The pain reported (8 inconsistent error envelopes, V7 ambiguity, V8 exit-code
   bug, `code`-vs-`reason` key drift) is **entirely on the error branch**.
   Fixing just errors removes every listed inconsistency.
2. Success payloads are already individually reasonable and are the ones with
   **exact-dict** test equalities and downstream-script exposure; leaving them
   untouched keeps churn mechanical and low-risk.
3. The two changes have different blast radii (errors ≈ 35 asserts; success ≈
   120). Coupling them forces the large, risky change to ship on the same PR as
   the cheap, high-value one.

Concrete sequence:

1. Add `_emit_error(code, message, *, exit_code=1, **extra)` to `cli.py`. Do
   **not** add `_emit`/wrapping yet.
2. Define the closed `code` set. Convert all ~18 error sites (§2 table) to
   `_emit_error`. Standardize on `{"error": {"code","message", ...extra}}`.
   - Map `VisibilityOpError.code` → `extra["refusal"]` (or promote it to the
     top-level `code` and use a coarse outer code — pick one and document it).
   - Map `ContextError.reason` → `extra["reason"]`; fold `symbol_id`/`detail`
     into extras.
   - Fold `plan-batch` `dropped` and `check --fix`/`privatize` `tx_id` into
     extras.
3. **Fix the two contract bugs while here:** route `scope`'s engine-level error
   (V8) through `_emit_error` so it exits 1; and stop grafting `error` onto
   `privatize`'s success dict (V7) — emit a clean error with the plan as
   `extra["plan"]`.
4. Update the ~35 error assertions across the ~10 test files. Add a regression
   test that every command's error output has `error.code` and `error.message`
   and a non-zero exit (a table-driven test would also lock the contract).
5. **Later / behind a decision:** if a uniform top-level is wanted, introduce
   `_emit` + the `{"ok":true,"data":…}` wrapper (Option A) as a **separate
   breaking bump**, ideally gated by a global `--envelope=wrapped|bare` flag or a
   major-version boundary, so success-payload consumers migrate deliberately.
6. **Orthogonal, optional:** add `check --json` emitting structured findings, so
   `check` joins the JSON contract without disturbing its default ruff-compatible
   text stream (§3).

Net: errors become a single predictable `{"error":{"code","message",...}}`
shape with codes an LLM can switch on, the exit-code contract is repaired, and
the stable success payloads (and their exact-dict tests) are left alone until a
deliberate envelope bump.
