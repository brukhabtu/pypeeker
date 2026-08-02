# Storage & Transaction Architecture

## Directory Structure

```
.pypeeker/
  index/
    src/
      auth/
        login.py.json       # symbols, scopes, refs for login.py
        session.py.json
      models/
        user.py.json
  transactions/
    <tx-id>.jsonl           # transaction logs (pending/applied/failed)
```

No global refs or imports files. Everything resolved on-demand from per-file indexes.

> **Naming note (rename with legacy read-fallback):** the on-disk directory is
> `.pypeeker/` (`STORAGE_DIR` in `src/pypeeker/storage/index_store.py`),
> matching the product and CLI name. It was formerly `.semantic-tool/` (a
> leftover from the working title, kept as `LEGACY_STORAGE_DIR`). To avoid a
> forced migration, `resolve_storage_root()` prefers `.pypeeker/` but falls
> back to a pre-existing `.semantic-tool/` when the new dir is absent, so a
> project indexed before the rename keeps using its existing directory (reads
> *and* writes) with no split state and no manual move; `find_project_root`
> treats either name as a project marker. New projects get `.pypeeker/`.

## Symbol IDs

Path-based format: `file:ScopeChain.With.Dots:local_with_colons`

**Examples:**

```python
# file: src/auth/service.py

class AuthService:                    # src/auth/service.py:AuthService
    
    def validate(self, token):        # src/auth/service.py:AuthService.validate
        # token param                 # src/auth/service.py:AuthService.validate:token
        
        result = check(token)         # src/auth/service.py:AuthService.validate:result


def helper():                         # src/auth/service.py:helper
    temp = 1                          # src/auth/service.py:helper:temp
```

**Pattern:**

- `.` separates named scopes (classes, functions)
- `:` separates locals/params within a scope

**Shadowing:**

Same name declared multiple times in same scope gets `$N` suffix by declaration order:

```text
process():
    data = fetch()        # process:data
    data = parse(data)    # process:data$2
    data = validate(data) # process:data$3
```

First occurrence has no suffix. Subsequent shadows get `$2`, `$3`, etc.

**Block scope:**

No `$block_N` tracking. Variables belong to their nearest function/method scope. This works because:
- Python: no block scope (variables in `if`/`for` belong to function)
- TypeScript/Rust/Mojo: block-scoped, but shadowing handled by `$N` suffix

IDs survive position changes but change on rename (which is fine - rename rewrites all references anyway).

This grammar is a **frozen, additive-only contract** (new sentinel prefixes may
be added; the separators `.`, `:`, `$` and the overall shape are stable), so
consumers and stored indexes can rely on it across versions.

**Target languages:** Python, TypeScript, Rust, Mojo

## Per-File Index

Each source file gets a corresponding JSON index file containing:
- Source file hash (for staleness detection)
- Symbols defined in that file
- Scopes within that file
- Outbound references from that file (to local and external symbols)

**Benefits:**
- Incremental updates - file changes, re-index just that file
- Parallel indexing - no write contention
- Natural invalidation - compare source hash to stored hash
- Partial loads - only load what you need for a query
- Mirrors source structure - easy to reason about

## Cross-File References

**No global refs file.** Each per-file index stores its own outbound references.

**On-demand validation:**
1. Before any operation, hash source files and compare to stored hash in index
2. Re-index stale files
3. Resolve references at query time
4. Dangling references discovered when queried, not watched

**"Find all references to X"** scans per-file indexes. O(files) but simple. Can optimise later if needed.

## Binder Phase

Parsing gives you syntax. Binding answers: "what does this name *refer to*?"

```python
def process():
    data = fetch()        # declaration: process:data
    data = parse(data)    # declaration: process:data$2, reference to process:data
    print(data)           # reference to process:data$2
```

**How it works:**

1. Walk the AST
2. Maintain a scope stack
3. Declaration → add to current scope's symbol table
4. Reference → look up scope chain, record binding

**Output:** For each reference, record "token at position X refers to symbol Y". This resolved binding goes in the per-file index.

## Transaction Log

Stored as JSONL in `transactions/<tx-id>.jsonl`. Each line is an edit operation.

**Lifecycle:**
1. `plan-*` command creates transaction file with status `pending`, records file hashes
2. Each planned edit appended as JSON line
3. `apply` verifies the transaction is `pending` and hashes match (abort if file changed; transaction stays `pending`)
4. Write edits to temp files first
5. Swap temp files to real locations
6. Success: delete temps, mark transaction `applied` (file retained so the rename can later be rolled back)
7. Failure mid-apply: swap back already-swapped files, mark transaction `failed`

Applied/failed transactions stay on disk (the header line carries the
status); only `pending` transactions can be applied. The `rolled_back` status
is set by the `rollback` command, which undoes an applied transaction and
marks it `ROLLED_BACK`. A pending transaction can be discarded before it is
applied with `transactions cancel <tx-id>`, which deletes its log file.

**Edit entry format:**
```json
{"op": "replace", "file": "src/auth/login.py", "start": 145, "end": 152, "old": "old_name", "new": "new_name", "file_hash": "abc123"}
```

Storing `old` value enables rollback. Storing `file_hash` enables conflict detection before write.

**Header version.** `TransactionHeader` carries a `version` field (default `1`).
`TransactionStore.save` writes `version = 2` only when the transaction contains at
least one file-creation or file-deletion entry (below); a transaction with only text
edits and/or a rename stays `version 1`, so every transaction already on disk reads
back unchanged with no migration. `load` refuses a `version` it does not understand —
or a line whose `op` is not recognised — with a clean, machine-readable error (a
`TransactionLoadError` carrying a stable `code`, e.g. `unsupported-transaction-version`
/ `unknown-transaction-op`) instead of raising a raw `TypeError` out of a malformed
field set. That `code` is surfaced, not just carried: every CLI command that loads a
transaction (`apply`, `rollback`, `transactions show`, `transactions cancel`, and the
apply-by-default tail of every mutating command) emits it through the standard
`{"error": ..., "code": ...}` envelope. `transactions list` degrades the single
unreadable file to an entry with `"status": "unreadable"` plus `error`/`code` rather
than aborting — one forward-format transaction must not make every readable one
invisible.

`TransactionHeader.created_dirs` (default `None`) is a separate, additive header
field, not a `version` bump: a missing key already reads back as `None` through the
serializer's normal default handling, so old transactions need no migration and stay
readable by old and new builds alike. It is stamped at apply time by
`TransactionStore.mark_applied` — the same single-header-line-rewrite mechanism as
`update_status` — not written by the planner. Because it is additive, `transactions
show` gains `header.created_dirs` in its output; that is within the frozen envelope's
additive-only rule.

`save` refuses to write a header that is already at a file-lifecycle version unless it
is handed both `creates` and `deletes` explicitly. The `LoadedTransaction` returned by
`load` is attribute-access only — its 3-element `(header, edits, file_rename)`
tuple-compat shim was retired in TASK-134 (see architecture.md → "Tuple-compat shims:
retired"). The refusal does not depend on that and is not weakened by it: it stands on
its own, stopping *any* read-modify-write that hands back only the fields it cared
about from silently erasing the create/delete lines and downgrading the header back to
`version 1`.

**File creation / deletion entry format.** A transaction may create or delete any
number of files (unlike rename, which is at most one per transaction). Both formats
are self-contained — no other source of truth is needed to reverse them.

The first producer of a `create_file` entry is the `move-symbol` planner
(`refactor/move.py`): moving a definition into a module that does not exist yet writes
the new module's full text as one creation, alongside the source file's deletion splice
and the importers' rewrites — one transaction for the whole move, so `rollback` removes
the newborn module and restores the definition in the same step. A move into a module
that *does* exist emits no creation at all; it is an ordinary splice appending to the
destination. Nothing emits `delete_file` from a CLI command yet — the entry type exists,
is round-tripped, and is emitted by `flatten_store` for any intent whose `Effect`
declares a death.

```json
{"op": "create_file", "path": "src/auth/new_module.py", "content": "def helper():\n    pass\n", "content_hash": "abc123"}
{"op": "delete_file", "path": "src/auth/old_module.py", "content": "def gone():\n    pass\n", "file_hash": "def456"}
```

- **Creation** (`create_file`): `content` is the full text to write — the pre-image of
  a creation is *absence*, not bytes, so there is deliberately no `file_hash`;
  pre-flight verifies the target does not yet exist. `content_hash` is the SHA-256 of
  `content`, used by rollback to refuse deleting a file someone edited after apply.
- **Deletion** (`delete_file`): `content` pins the full pre-image so rollback can
  restore the file byte-for-byte. `file_hash` keeps the same meaning as an edit entry's
  — the SHA-256 of the file at plan time — and is verified at pre-flight exactly like a
  text edit's.

**Pre-flight is three checks, not one.** Each is what makes a later step safe:

1. *Entries against each other.* A path may be claimed by at most one file-level entry,
   and a file-level entry's path may not also be edited — with exactly one exception,
   editing a file and then renaming it (the module-rename shape, where the applier's
   phase order is defined and rollback maps the edited path through the rename). Every
   other pairing is refused before anything is touched, because both entries can
   individually pass every check and still destroy each other at commit: an edit plus a
   deletion of one path commits the edit and then unlinks it, so rollback can never find
   the file again; a creation plus a rename onto the same target lets the rename silently
   overwrite the newborn; a deletion plus a rename onto the freed path applies cleanly
   and then refuses rollback forever, since the deleted file's path is occupied. The
   result in each case is a transaction that ends `applied` with no inverse.
2. *Entries against themselves.* `content` must hash to the hash the entry pins
   (`content_hash` for a creation, `file_hash` for a deletion). `content` is a `str`
   while the hashes are over bytes, so a producer that decoded the file lossily — a
   universal-newlines `read_text()` over a CRLF file is the realistic case — would
   satisfy the on-disk hash check and still restore *different bytes* on rollback. That
   is silent corruption of the byte-for-byte inverse, so it is refused as a malformed
   entry.
3. *Entries against the tree.* Every edited/deleted file exists and hash-matches; every
   created path does not exist.

`apply` stages creation content to a `.tmp` file (never the real path) and stages
deletion bytes into memory, commits by swapping creation temps into place and then
unlinking deletions, and — on a mid-apply failure — restores edited files from backup,
recreates deleted files from their pinned content, and unlinks created files (safe only
because pre-flight just proved those paths did not exist) before marking the
transaction `failed`. Re-indexing removes a deleted file's index entry and adds a
created file's; `rollback` does the inverse (removes what was created, restores what
was deleted) — no ghost index entry survives either direction.

**Directories count as mutation.** A creation — or a file rename whose target sits in one
— whose path needs a directory that does not exist yet conjures it, and that directory is
undone with the file. `apply` records
exactly the directories it conjured on the header as `TransactionHeader.created_dirs`
(outermost-first, project-root-relative POSIX paths) in the same write that marks the
transaction `applied` (`TransactionStore.mark_applied`). The mid-apply failure path
removes that same in-memory list; `rollback` removes exactly the recorded set,
deepest-first and only while still empty, after every other file the rollback restores
is back in place — it does **not** walk up from the removed file guessing by emptiness,
because a directory that is merely empty at that point may have predated the
transaction, and rollback does not own it. Under PEP 420 a leftover empty directory is
an importable namespace package, so a `failed` apply or a completed rollback that left
a *conjured* one behind would not have restored the tree — and repeated plan/rollback
cycles would accumulate them.

`created_dirs` is `None` on a header written by a build predating this field — no
directory evidence, so rollback removes nothing for such a transaction. That is the
conservative direction: it can leave behind a PEP 420-importable empty directory (residue)
rather than risk removing a directory the transaction never created (destruction), and it
can only ever affect a transaction that was `applied` before the upgrade — no new
transaction can ever lack the field.

Both ends of the record keep it inside the project root, an invariant the walk-up pruning
got structurally (it stopped before reaching the root) and a persisted path list has to
get deliberately. Writing: the relative paths are derived during staging, next to the
`mkdir` that produced them and inside the failure handler's reach, so an entry path that
cannot be expressed relative to the root ends the transaction `failed` with its tree
restored, rather than raising after the commit under a still-`pending` header with no
inverse. Reading: `rollback` resolves each recorded entry and skips any that is not a
strict descendant of the project root — `project_root / d` is not a containment operator,
since an absolute `d` discards the root and a leading `..` walks above it, and a corrupt
or hand-edited record must not be able to point an `rmdir` outside the project. A skipped
entry leaves an empty directory behind, the same residue-not-destruction direction as the
`None` case.

## Execution Rules

**Within a file:** Apply edits bottom-to-top (reverse position order) so earlier edits don't shift positions of later ones.

**Across files:** Order doesn't matter. Write to temp files, then swap all.

**Conflict detection:** Before applying, verify file hashes match what was recorded at plan time. If any file changed, abort and require re-plan.

## Rename Cascades

**Default: Minimal**

Rename only the symbol itself and its references. Nothing else. Predictable, no surprises.

**Opt-in: Convention-aware**

Flags to include related changes:

```
pypeeker rename <symbol> <new-name> --include-file --include-exports
```

(Every mutating command, `rename` included, plans AND applies immediately by
default since TASK-126; add `--plan` to only write the transaction PENDING —
see architecture.md's "Output contract" for the full grammar.)

- `--include-file` - rename the containing file if it matches symbol name (e.g., Python's `user_service.py` for `UserService`)
- `--include-exports` - update barrel files, `__init__.py`, re-exports
- `--include-receivers` - also rename attribute/method references reached through receiver inference
- `--keep-export` - rename the definition but preserve its public export name, rewriting the `__init__` re-export to `NewName as Old` (mutually exclusive with `--include-exports`)

**Move cascades: repair, not propagation**

`pypeeker move-symbol <symbol-id> <dest-module>` rewrites every `from … import` that
binds the moved definition — including package `__init__` re-exports, which are rewritten
**unconditionally** with no flag. That is not a cascade: a move does not change the
exported *name*, so pointing the re-export at the definition's new home is repair of a
statement the move would otherwise have broken. A consumer that reaches the symbol
*through* such a re-export (`from pkg import X`) is left untouched — the barrel it goes
through is repaired, so its own statement is still correct, and editing it anyway would
be propagation.

The qualified `import m` + `m.name` form is **refused**, not half-rewritten: repairing it
means rewriting receivers at every call site, which is a different edit shape from an
import line, and moving the definition while leaving `m.name` behind produces code that
imports cleanly and fails at the call.

**Explicitly avoided: Semantic cascades**

The tool will NOT automatically rename related symbols (e.g., `User` → `Account` doesn't auto-rename `UserService`). That's heuristic-based and produces surprises.

Let the human or LLM make those decisions by running multiple explicit renames.

**Principle: Precise, not clever.**

## Error Recovery

**Policy: Best-effort rollback.**

If a multi-file refactoring fails partway through swap, swap back completed files from temps.

**Atomicity:** Best-effort, not transactional. Small window for corruption exists if process killed mid-swap, but acceptable for v1.

## Concurrency

**Policy: Not supported.**

Don't run multiple `pypeeker` instances simultaneously on the same project. No file locking or daemon coordination in v1.

## In-Memory Model

At runtime:
1. Load relevant per-file indexes into memory
2. Build unified symbol table and scope tree
3. Query against in-memory model
4. Persist changes back to per-file JSON

JSONL transactions are append-only during planning, read sequentially during apply.
