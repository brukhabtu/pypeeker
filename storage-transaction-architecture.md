# Storage & Transaction Architecture

## Directory Structure

```
.semantic-tool/
  index/
    src/
      auth/
        login.py.json       # symbols, scopes, refs for login.py
        session.py.json
      models/
        user.py.json
  transactions/
    <tx-id>.jsonl           # pending transaction logs
```

No global refs or imports files. Everything resolved on-demand from per-file indexes.

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

```rust
fn process() {
    let data = fetch();        // process:data
    let data = parse(data);    // process:data$2  
    let data = validate(data); // process:data$3
}
```

First occurrence has no suffix. Subsequent shadows get `$2`, `$3`, etc.

**Block scope:**

No `$block_N` tracking. Variables belong to their nearest function/method scope. This works because:
- Python: no block scope (variables in `if`/`for` belong to function)
- TypeScript/Rust/Mojo: block-scoped, but shadowing handled by `$N` suffix

IDs survive position changes but change on rename (which is fine - rename rewrites all references anyway).

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
1. `plan-*` command creates transaction file, records file hashes
2. Each planned edit appended as JSON line
3. `apply` verifies hashes match (abort if file changed)
4. Write edits to temp files first
5. Swap temp files to real locations
6. Success: delete transaction file and temps
7. Failure: swap back from temps, delete transaction file

**Edit entry format:**
```json
{"op": "replace", "file": "src/auth/login.py", "start": 145, "end": 152, "old": "old_name", "new": "new_name", "file_hash": "abc123"}
```

Storing `old` value enables rollback. Storing `file_hash` enables conflict detection before write.

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
semantic-tool plan-rename <symbol> <new-name> --include-file --include-exports
```

- `--include-file` - rename the containing file if it matches symbol name (e.g., Python's `user_service.py` for `UserService`)
- `--include-exports` - update barrel files, `__init__.py`, re-exports

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

Don't run multiple `semantic-tool` instances simultaneously on the same project. No file locking or daemon coordination in v1.

## In-Memory Model

At runtime:
1. Load relevant per-file indexes into memory
2. Build unified symbol table and scope tree
3. Query against in-memory model
4. Persist changes back to per-file JSON

JSONL transactions are append-only during planning, read sequentially during apply.
