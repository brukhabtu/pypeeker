"""CLI entry point for pypeeker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.indexer import (
    PathNotFoundError,
    ensure_fresh,
    find_project_root,
    index_path,
)
from pypeeker.models.serialize import to_dict
from pypeeker.models.transaction import TransactionStatus
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.storage import IndexStore, TransactionStore, TreeStore


def _no_refresh_option(command):
    """Shared ``--no-refresh`` opt-out for commands that read the index."""
    return click.option(
        "--no-refresh",
        is_flag=True,
        default=False,
        help="Skip refreshing stale index entries first (may serve stale data).",
    )(command)


def _refresh_index(ctx: click.Context, no_refresh: bool) -> None:
    """Re-index stale files (and drop deleted ones) before serving a command.

    Only files already in the index are touched; a never-indexed project is
    left alone. Skipped entirely when the user passed ``--no-refresh``.
    """
    if no_refresh:
        return
    ensure_fresh(ctx.obj["store"], ctx.obj["root"], adapter=ctx.obj["adapter"])


def _engine(ctx: click.Context) -> SemanticQueryEngine:
    """Build a query engine from the stores constructed in the group callback."""
    return SemanticQueryEngine(ctx.obj["store"], ctx.obj["tree_store"])


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """pypeeker - Semantic code intelligence for Python."""
    ctx.ensure_object(dict)
    root = find_project_root()
    # Composition root: every store is constructed exactly once here and
    # injected into the layers below — no command or engine builds its own.
    ctx.obj["store"] = IndexStore(root)
    ctx.obj["transaction_store"] = TransactionStore(root)
    ctx.obj["tree_store"] = TreeStore(root)
    ctx.obj["adapter"] = PythonAdapter()
    ctx.obj["root"] = root


@main.command()
@click.argument("path")
@click.pass_context
def index(ctx: click.Context, path: str) -> None:
    """Index a file or directory.

    PATH can be a single .py file or a directory (indexes all .py files recursively).
    """
    try:
        result = index_path(
            Path(path).resolve(),
            store=ctx.obj["store"],
            root=ctx.obj["root"],
            adapter=ctx.obj["adapter"],
        )
    except PathNotFoundError:
        click.echo(json.dumps({"error": f"Path not found: {path}"}))
        sys.exit(1)

    from pypeeker.treebuild import load_or_rebuild

    load_or_rebuild(ctx.obj["store"], ctx.obj["tree_store"])

    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command()
@click.option(
    "--baseline",
    "use_baseline",
    is_flag=True,
    default=False,
    help=(
        "Compare against the stored baseline (.semantic-tool/check-baseline.json): "
        "print and fail only on NEW violations. A missing baseline file counts "
        "as empty (every violation is new)."
    ),
)
@click.option(
    "--update-baseline",
    is_flag=True,
    default=False,
    help=(
        "Run all rules and record the current violations as the new baseline "
        "(fixed violations shrink it), then exit 0."
    ),
)
@_no_refresh_option
@click.pass_context
def check(
    ctx: click.Context, use_baseline: bool, update_baseline: bool, no_refresh: bool
) -> None:
    """Run semantic lint rules declared in [tool.pypeeker] of pyproject.toml.

    Exits non-zero if any violations are found. Output format matches
    ruff/mypy: 'path:line: [rule] message'. Stale index entries are
    re-indexed first unless --no-refresh is given.

    With --baseline, only violations NOT covered by the recorded baseline are
    printed and counted toward the exit code, followed by a one-line summary.
    With --update-baseline, the current violations replace the baseline.
    Violation identity in the baseline is line-independent, so unrelated
    edits that shift line numbers never re-fire baselined violations.
    """
    from pypeeker.check import CheckEngine, load_config
    from pypeeker.check.baseline import (
        baseline_path,
        delta,
        load_baseline,
        write_baseline,
    )

    if use_baseline and update_baseline:
        raise click.UsageError(
            "--baseline and --update-baseline are mutually exclusive: "
            "compare first, then update."
        )

    _refresh_index(ctx, no_refresh)
    store: IndexStore = ctx.obj["store"]
    root: Path = ctx.obj["root"]
    engine = CheckEngine(store, load_config(root))
    violations = engine.run()

    if update_baseline:
        counts = write_baseline(baseline_path(root), violations)
        click.echo(
            f"baseline updated: {sum(counts.values())} violation(s) recorded "
            f"in {baseline_path(root).relative_to(root)}"
        )
        return

    if use_baseline:
        baseline = load_baseline(baseline_path(root))
        new, fixed = delta(violations, baseline)
        for v in new:
            click.echo(str(v))
        click.echo(f"{sum(baseline.values())} baselined, {len(new)} new, {len(fixed)} fixed")
        if new:
            sys.exit(1)
        return

    for v in violations:
        click.echo(str(v))
    if violations:
        sys.exit(1)


@main.command()
@click.argument("name")
@_no_refresh_option
@click.pass_context
def symbol(ctx: click.Context, name: str, no_refresh: bool) -> None:
    """Look up a symbol by name or ID.

    NAME can be a simple name ("validate"), partial ID ("AuthService.validate"),
    or full ID ("src/auth/service.py:AuthService.validate"). Stale index
    entries are re-indexed first unless --no-refresh is given.
    """
    _refresh_index(ctx, no_refresh)
    engine = _engine(ctx)
    symbols = engine.find_symbol(name)
    output = [to_dict(s) for s in symbols]
    click.echo(json.dumps(output, indent=2))


@main.command()
@click.argument("symbol_id")
@click.option(
    "--all",
    "follow_imports",
    is_flag=True,
    help=(
        "Match the symbol's resolved definition instead of its exact "
        "binding: include usages reached through imports, __init__.py "
        "re-exports, and receiver attribute access (crosses modules)."
    ),
)
@_no_refresh_option
@click.pass_context
def refs(
    ctx: click.Context, symbol_id: str, follow_imports: bool, no_refresh: bool
) -> None:
    """Find references to a symbol.

    SYMBOL_ID is the full symbol ID (e.g., "pkg.mod:AuthService.validate").

    By default, only references whose binding is exactly SYMBOL_ID are
    returned — same-binding usages only. A consumer module's usages bind to
    its local import symbol, not to the definition, so the default does NOT
    cross module boundaries; the output is the plain reference objects.

    With --all, references are matched against the symbol's resolved
    *definition*: usages of that definition reached through import aliases,
    __init__.py re-exports, and receiver attribute access are included, and
    each JSON item carries an extra "resolution" field saying how the match
    resolved: "direct" (binds straight to the definition), "import_alias"
    (through imports, no barrel), "barrel" (through an __init__.py
    re-export), "receiver_declared" (attribute access resolved via declared
    annotations / self / cls / module or class receivers), or
    "receiver_inferred" (the receiver walk relied on a constructor-inferred
    type — lowest confidence). Stale index entries are re-indexed first
    unless --no-refresh is given.
    """
    _refresh_index(ctx, no_refresh)
    engine = _engine(ctx)
    if follow_imports:
        output = [
            {**to_dict(r.reference), "resolution": r.via.value}
            for r in engine.references_to_definition_classified(symbol_id)
        ]
    else:
        output = [to_dict(r) for r in engine.references_to_binding(symbol_id)]
    click.echo(json.dumps(output, indent=2))


@main.command()
@click.argument("symbol_id", required=False)
@_no_refresh_option
@click.pass_context
def tree(ctx: click.Context, symbol_id: str | None, no_refresh: bool) -> None:
    """Show the package/module symbol tree.

    With no argument, prints the root package/module nodes. With a SYMBOL_ID
    (a dotted package/module path, or a class/function id), prints that node's
    direct members. Stale index entries are re-indexed first unless
    --no-refresh is given.
    """
    _refresh_index(ctx, no_refresh)
    engine = _engine(ctx)
    if symbol_id is None:
        tree_index = engine.get_tree()
        output = [to_dict(tree_index.nodes[nid]) for nid in tree_index.root_ids]
    else:
        output = engine.members(symbol_id)
    click.echo(json.dumps(output, indent=2))


@main.command()
@click.argument("symbol_id")
@_no_refresh_option
@click.pass_context
def purity(ctx: click.Context, symbol_id: str, no_refresh: bool) -> None:
    """Report a purity verdict for a function, with impurity observations.

    SYMBOL_ID identifies a function or method (name, partial ID, or full ID).
    Emits a JSON verdict: "pure": true means no impurity was found by the
    configured policy — not that the function is provably pure. Observations
    include direct impurities (writes, calls) and transitive calls into
    impure project functions. Unanalyzable symbols (not found, not a
    function) produce a structured error and a non-zero exit. Stale index
    entries are re-indexed first unless --no-refresh is given.
    """
    from pypeeker.analysis.context import AnalysisContext, ContextError
    from pypeeker.analysis.purity import impurities

    _refresh_index(ctx, no_refresh)
    store: IndexStore = ctx.obj["store"]
    engine = _engine(ctx)
    analysis_ctx = AnalysisContext.for_function(store, symbol_id, engine=engine)
    if isinstance(analysis_ctx, ContextError):
        click.echo(
            json.dumps(
                {
                    "error": f"Cannot analyze '{symbol_id}': {analysis_ctx.reason}",
                    "reason": analysis_ctx.reason,
                    "symbol_id": analysis_ctx.symbol_id,
                    "detail": analysis_ctx.detail,
                },
                indent=2,
            )
        )
        sys.exit(1)

    resolved_id = analysis_ctx.function_symbol.symbol_id
    result = impurities(store, resolved_id, engine=engine)
    if result is None:  # pragma: no cover — context resolved above
        click.echo(
            json.dumps(
                {
                    "error": f"Cannot analyze '{symbol_id}'",
                    "reason": "not_found_or_not_a_function",
                }
            )
        )
        sys.exit(1)

    observations = [
        {"kind": type(obs).__name__, **to_dict(obs)} for obs in result
    ]
    click.echo(
        json.dumps(
            {
                "symbol_id": resolved_id,
                "pure": not result,
                "observations": observations,
            },
            indent=2,
        )
    )


@main.command("plan-extract-variable")
@click.argument("file_path")
@click.argument("start")
@click.argument("end")
@click.argument("name")
@_no_refresh_option
@click.pass_context
def plan_extract_variable(
    ctx: click.Context,
    file_path: str,
    start: str,
    end: str,
    name: str,
    no_refresh: bool,
) -> None:
    """Plan extracting a selected expression into a new variable.

    START and END are 0-indexed "line:col" positions bounding the expression.
    Creates a transaction applied with the 'apply' command. Stale index
    entries are re-indexed first unless --no-refresh is given.
    """
    from pypeeker.refactor.extract import ExtractVariableError, ExtractVariablePlanner

    def _pos(s: str) -> tuple[int, int]:
        line, col = s.split(":", 1)
        return int(line), int(col)

    _refresh_index(ctx, no_refresh)
    planner = ExtractVariablePlanner(
        ctx.obj["store"], ctx.obj["transaction_store"]
    )
    try:
        summary = planner.plan(file_path, _pos(start), _pos(end), name)
    except (ExtractVariableError, ValueError) as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)
    click.echo(json.dumps(to_dict(summary), indent=2))


@main.command("plan-inline-variable")
@click.argument("symbol_id")
@_no_refresh_option
@click.pass_context
def plan_inline_variable(ctx: click.Context, symbol_id: str, no_refresh: bool) -> None:
    """Plan inlining a local variable into its uses (and deleting it).

    SYMBOL_ID is the variable's full id (e.g. "m:f:x"). Refuses reassigned
    variables, and impure values used more than once. Creates a transaction
    applied with the 'apply' command. Stale index entries are re-indexed
    first unless --no-refresh is given.
    """
    from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner

    _refresh_index(ctx, no_refresh)
    planner = InlineVariablePlanner(ctx.obj["store"], ctx.obj["transaction_store"])
    try:
        summary = planner.plan(symbol_id)
    except InlineVariableError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)
    click.echo(json.dumps(to_dict(summary), indent=2))


@main.command("plan-extract-method")
@click.argument("file_path")
@click.argument("start_line", type=int)
@click.argument("end_line", type=int)
@click.argument("name")
@_no_refresh_option
@click.pass_context
def plan_extract_method(
    ctx: click.Context,
    file_path: str,
    start_line: int,
    end_line: int,
    name: str,
    no_refresh: bool,
) -> None:
    """Plan extracting a statement range into a new top-level function.

    START_LINE and END_LINE are 0-indexed, inclusive. Parameters and return
    values are derived from data flow; ranges with return/break/continue are
    refused. Creates a transaction applied with the 'apply' command. Stale
    index entries are re-indexed first unless --no-refresh is given.
    """
    from pypeeker.refactor.extract import ExtractMethodError, ExtractMethodPlanner

    _refresh_index(ctx, no_refresh)
    planner = ExtractMethodPlanner(ctx.obj["store"], ctx.obj["transaction_store"])
    try:
        summary = planner.plan(file_path, start_line, end_line, name)
    except ExtractMethodError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)
    click.echo(json.dumps(to_dict(summary), indent=2))


@main.command()
@click.argument("location")
@_no_refresh_option
@click.pass_context
def scope(ctx: click.Context, location: str, no_refresh: bool) -> None:
    """Show what's visible at a location.

    LOCATION format: "file_path:line_number" (e.g., "src/auth/service.py:15").
    Stale index entries are re-indexed first unless --no-refresh is given.
    """
    _refresh_index(ctx, no_refresh)
    engine = _engine(ctx)
    # Split on last colon to handle file paths with colons
    parts = location.rsplit(":", 1)
    if len(parts) != 2:
        click.echo(json.dumps({"error": f"Invalid location format: {location}"}))
        sys.exit(1)

    file_path, line_str = parts
    try:
        line = int(line_str)
    except ValueError:
        click.echo(json.dumps({"error": f"Invalid line number: {line_str}"}))
        sys.exit(1)

    result = engine.get_scope_at(file_path, line)
    click.echo(json.dumps(result, indent=2, default=str))


@main.command("plan-rename")
@click.argument("symbol_id")
@click.argument("new_name")
@click.option(
    "--include-file",
    is_flag=True,
    default=False,
    help="Rename containing file if it matches symbol name.",
)
@click.option(
    "--include-exports",
    is_flag=True,
    default=False,
    help="Update barrel files, __init__.py, re-exports.",
)
@click.option(
    "--include-receivers",
    is_flag=True,
    default=False,
    help=(
        "Also rename method/attribute call sites resolved through a receiver "
        "(declared-type, self/cls, module/class only — not inferred)."
    ),
)
@click.option(
    "--keep-export",
    is_flag=True,
    default=False,
    help=(
        "Rename the definition but preserve its public package export name "
        "(rewrites the __init__ re-export to 'New as Old'). Mutually exclusive "
        "with --include-exports."
    ),
)
@_no_refresh_option
@click.pass_context
def plan_rename(
    ctx: click.Context,
    symbol_id: str,
    new_name: str,
    include_file: bool,
    include_exports: bool,
    include_receivers: bool,
    keep_export: bool,
    no_refresh: bool,
) -> None:
    """Plan a symbol rename.

    SYMBOL_ID is the symbol to rename (name, partial ID, or full ID).
    NEW_NAME is the new name for the symbol. Stale index entries are
    re-indexed first unless --no-refresh is given.

    Creates a transaction plan that can be applied with the 'apply' command.
    """
    from pypeeker.refactor.planner import RenamePlanError, RenamePlanner

    _refresh_index(ctx, no_refresh)
    store: IndexStore = ctx.obj["store"]
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    planner = RenamePlanner(store, transaction_store)

    try:
        summary = planner.plan(
            symbol_id,
            new_name,
            include_file=include_file,
            include_exports=include_exports,
            include_receivers=include_receivers,
            keep_export=keep_export,
        )
        click.echo(json.dumps(to_dict(summary), indent=2))
    except RenamePlanError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)


@main.command()
@click.argument("tx_id")
@click.pass_context
def apply(ctx: click.Context, tx_id: str) -> None:
    """Apply a planned transaction.

    TX_ID is the transaction ID from a plan-rename command.
    Verifies file integrity before applying and re-indexes affected files.
    """
    from pypeeker.refactor.applier import ApplyError, TransactionApplier

    store: IndexStore = ctx.obj["store"]
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    applier = TransactionApplier(store, transaction_store)

    try:
        result = applier.apply(tx_id)
        click.echo(json.dumps(result, indent=2))
    except ApplyError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)


@main.command()
@click.argument("tx_id")
@click.pass_context
def rollback(ctx: click.Context, tx_id: str) -> None:
    """Roll back an applied transaction.

    TX_ID is the transaction ID of an APPLIED transaction. Verifies the
    affected files still hold the post-apply content (refusing if they were
    modified since apply — no partial rollback), restores the stored
    pre-apply text, reverses any file rename, re-indexes the affected
    files, and marks the transaction ROLLED_BACK.
    """
    from pypeeker.refactor.applier import RollbackError, TransactionApplier

    store: IndexStore = ctx.obj["store"]
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    applier = TransactionApplier(store, transaction_store)

    try:
        result = applier.rollback(tx_id)
        click.echo(json.dumps(result, indent=2))
    except RollbackError as e:
        click.echo(json.dumps({"error": str(e)}))
        sys.exit(1)


@main.group()
def transactions() -> None:
    """Inspect and manage refactor transactions.

    Transaction lifecycle: a plan-* command writes a PENDING transaction;
    'apply' executes it and marks it APPLIED (or FAILED on a mid-apply
    error); 'rollback' restores an APPLIED transaction's files and marks
    it ROLLED_BACK. Use 'transactions cancel' to delete a PENDING
    transaction that should never be applied.
    """


@transactions.command("list")
@click.pass_context
def transactions_list(ctx: click.Context) -> None:
    """List every transaction with status and affected files."""
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    output = []
    for tx_id in transaction_store.list():
        loaded = transaction_store.load(tx_id)
        if loaded is None:  # pragma: no cover — listed ids exist on disk
            continue
        header, edits, file_rename = loaded
        files = {edit.file for edit in edits}
        if file_rename:
            files.update({file_rename.old_path, file_rename.new_path})
        output.append(
            {
                "tx_id": header.tx_id,
                "operation": header.operation,
                "status": header.status.value,
                "created_at": header.created_at,
                "edit_count": len(edits) + (1 if file_rename else 0),
                "files_affected": sorted(files),
            }
        )
    click.echo(json.dumps(output, indent=2))


@transactions.command("show")
@click.argument("tx_id")
@click.pass_context
def transactions_show(ctx: click.Context, tx_id: str) -> None:
    """Show a transaction's header and full edit list.

    TX_ID is the transaction ID from a plan-* command.
    """
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    loaded = transaction_store.load(tx_id)
    if loaded is None:
        click.echo(json.dumps({"error": f"Transaction not found: {tx_id}"}))
        sys.exit(1)
    header, edits, file_rename = loaded
    output = {
        "header": to_dict(header),
        "edits": [to_dict(edit) for edit in edits],
        "file_rename": to_dict(file_rename) if file_rename else None,
    }
    click.echo(json.dumps(output, indent=2))


@transactions.command("cancel")
@click.argument("tx_id")
@click.pass_context
def transactions_cancel(ctx: click.Context, tx_id: str) -> None:
    """Cancel (delete) a PENDING transaction.

    TX_ID is the transaction ID from a plan-* command. Only pending
    transactions can be cancelled; applied transactions are retained so
    they can be rolled back with 'rollback'.
    """
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    loaded = transaction_store.load(tx_id)
    if loaded is None:
        click.echo(json.dumps({"error": f"Transaction not found: {tx_id}"}))
        sys.exit(1)
    header, _, _ = loaded
    if header.status != TransactionStatus.PENDING:
        click.echo(
            json.dumps(
                {
                    "error": (
                        f"Only pending transactions can be cancelled; "
                        f"{tx_id} is {header.status.value}"
                    )
                }
            )
        )
        sys.exit(1)
    transaction_store.remove(tx_id)
    click.echo(json.dumps({"tx_id": tx_id, "status": "cancelled"}, indent=2))
