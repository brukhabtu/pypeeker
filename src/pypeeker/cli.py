"""CLI entry point for pypeeker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from pypeeker.adapters.python_adapter import PythonAdapter
from pypeeker.indexer import PathNotFoundError, find_project_root, index_path
from pypeeker.models.serialize import to_dict
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.storage import IndexStore, TransactionStore


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """pypeeker - Semantic code intelligence for Python."""
    ctx.ensure_object(dict)
    root = find_project_root()
    ctx.obj["store"] = IndexStore(root)
    ctx.obj["transaction_store"] = TransactionStore(root)
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

    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command()
@click.argument("name")
@click.pass_context
def symbol(ctx: click.Context, name: str) -> None:
    """Look up a symbol by name or ID.

    NAME can be a simple name ("validate"), partial ID ("AuthService.validate"),
    or full ID ("src/auth/service.py:AuthService.validate").
    """
    engine = SemanticQueryEngine(ctx.obj["store"])
    symbols = engine.find_symbol(name)
    output = [to_dict(s) for s in symbols]
    click.echo(json.dumps(output, indent=2))


@main.command()
@click.argument("symbol_id")
@click.pass_context
def refs(ctx: click.Context, symbol_id: str) -> None:
    """Find all references to a symbol.

    SYMBOL_ID is the full symbol ID (e.g., "src/auth/service.py:AuthService.validate").
    """
    engine = SemanticQueryEngine(ctx.obj["store"])
    references = engine.find_references(symbol_id)
    output = [to_dict(r) for r in references]
    click.echo(json.dumps(output, indent=2))


@main.command()
@click.argument("location")
@click.pass_context
def scope(ctx: click.Context, location: str) -> None:
    """Show what's visible at a location.

    LOCATION format: "file_path:line_number" (e.g., "src/auth/service.py:15").
    """
    engine = SemanticQueryEngine(ctx.obj["store"])
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
@click.pass_context
def plan_rename(
    ctx: click.Context,
    symbol_id: str,
    new_name: str,
    include_file: bool,
    include_exports: bool,
) -> None:
    """Plan a symbol rename.

    SYMBOL_ID is the symbol to rename (name, partial ID, or full ID).
    NEW_NAME is the new name for the symbol.

    Creates a transaction plan that can be applied with the 'apply' command.
    """
    from pypeeker.refactor.planner import RenamePlanError, RenamePlanner

    store: IndexStore = ctx.obj["store"]
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    planner = RenamePlanner(store, transaction_store)

    try:
        summary = planner.plan(
            symbol_id,
            new_name,
            include_file=include_file,
            include_exports=include_exports,
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
