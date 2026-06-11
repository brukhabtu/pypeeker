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


def _split_by_confidence(violations: list, strict: bool) -> tuple[list, int]:
    """Partition check findings for display by confidence tier.

    Returns ``(shown, hidden_count)``. With ``strict`` everything is shown;
    otherwise HEURISTIC/UNKNOWN findings are hidden and only counted —
    DECLARED and INFERRED findings always show. Display-only: baseline
    storage and comparison always operate on the full violation set.
    """
    if strict:
        return violations, 0
    from pypeeker.models.capabilities import Confidence

    low = (Confidence.HEURISTIC, Confidence.UNKNOWN)
    shown = [v for v in violations if v.confidence not in low]
    return shown, len(violations) - len(shown)


def _echo_hidden_note(hidden: int) -> None:
    """Summarize hidden low-confidence findings (no-op when none were hidden)."""
    if hidden:
        click.echo(
            f"{hidden} low-confidence violation(s) hidden (use --strict to show)"
        )


def _apply_check_fixes(ctx: click.Context, engine, violations: list, strict: bool) -> None:
    """Plan, de-conflict, and apply violation-attached fixes (``check --fix``).

    * Only fixes on certain (DECLARED-confidence) findings are planned;
      heuristic/inferred/unknown findings never auto-apply.
    * Planned fixes are considered in deterministic (file, start, fix_id)
      order; a fix whose byte ranges overlap an already-kept fix in the same
      file is skipped as a conflict — one fix per file region, across rules.
    * The surviving edits are written as ONE ``check-fix`` transaction and
      applied immediately through :class:`TransactionApplier`, so the
      standard lifecycle holds: hashes are verified before writing, edited
      files are re-indexed, and the APPLIED transaction stays on disk for
      ``rollback <tx_id>`` / ``transactions show <tx_id>``.
    * Check is re-run after the apply and the residual count reported.

    Prints a JSON report ``{applied, skipped_conflicts, declined,
    residual_violations, tx_id}`` and exits non-zero when violations remain
    (the residual count honors the default confidence display filter unless
    ``--strict``, matching plain ``check``).
    """
    import uuid
    from datetime import datetime, timezone

    from pypeeker.check.fixes import FixPlan
    from pypeeker.models.capabilities import Confidence
    from pypeeker.models.transaction import TransactionHeader
    from pypeeker.refactor.applier import ApplyError, TransactionApplier

    store: IndexStore = ctx.obj["store"]
    transaction_store: TransactionStore = ctx.obj["transaction_store"]

    declined: list[dict] = []
    planned: list[tuple] = []
    for violation in violations:
        if violation.fix is None:
            continue
        if violation.confidence is not Confidence.DECLARED:
            continue  # only certain findings auto-fix
        outcome = violation.fix.plan(store)
        if isinstance(outcome, FixPlan):
            planned.append((violation, outcome))
        else:
            declined.append(
                {
                    "fix_id": outcome.fix_id,
                    "reason": outcome.reason.value,
                    "detail": outcome.detail,
                }
            )

    def _order(item: tuple) -> tuple:
        """Deterministic application order: earliest edit, then fix_id."""
        _, plan = item
        first = min((edit.file, edit.start) for edit in plan.edits)
        return (first[0], first[1], plan.fix_id)

    planned.sort(key=_order)
    applied: list[dict] = []
    skipped_conflicts: list[dict] = []
    kept_plans: list[FixPlan] = []
    claimed: dict[str, list[tuple[int, int]]] = {}
    for violation, plan in planned:
        entry = {
            "fix_id": plan.fix_id,
            "description": plan.description,
            "violation": str(violation),
        }
        conflicts = any(
            edit.start < end and start < edit.end
            for edit in plan.edits
            for start, end in claimed.get(edit.file, ())
        )
        if conflicts:
            skipped_conflicts.append(entry)
            continue
        kept_plans.append(plan)
        applied.append(entry)
        for edit in plan.edits:
            claimed.setdefault(edit.file, []).append((edit.start, edit.end))

    tx_id: str | None = None
    if kept_plans:
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id="",
            old_name="",
            new_name="",
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="check-fix",
        )
        transaction_store.save(
            header, [edit for plan in kept_plans for edit in plan.edits]
        )
        try:
            TransactionApplier(store, transaction_store).apply(tx_id)
        except ApplyError as e:
            click.echo(json.dumps({"error": str(e), "tx_id": tx_id}))
            sys.exit(1)
        residual = engine.run()  # the applier re-indexed the edited files
    else:
        residual = violations

    shown, _hidden = _split_by_confidence(residual, strict)
    click.echo(
        json.dumps(
            {
                "applied": applied,
                "skipped_conflicts": skipped_conflicts,
                "declined": declined,
                "residual_violations": len(shown),
                "tx_id": tx_id,
            },
            indent=2,
        )
    )
    if shown:
        sys.exit(1)


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
        "(fixed violations shrink it), then exit 0. Always records the FULL "
        "set, including low-confidence violations --strict would reveal."
    ),
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help=(
        "Include low-confidence (heuristic/unknown) violations in output and "
        "exit code. By default they are hidden and only summarized; "
        "declared/inferred findings always show. Output marks non-certain "
        "tiers with a trailing [tier]. Baselines are unaffected: "
        "--update-baseline records and --baseline compares the full set "
        "regardless of this flag."
    ),
)
@click.option(
    "--fix",
    "apply_fixes",
    is_flag=True,
    default=False,
    help=(
        "Apply every autofix attached to a certain-confidence violation as "
        "ONE transaction (revert with 'rollback <tx_id>', inspect with "
        "'transactions show <tx_id>'). Fixes that decline to plan are "
        "reported; overlapping fixes are skipped deterministically (first "
        "by file/offset wins). Prints a JSON report and exits non-zero when "
        "violations remain afterwards. Mutually exclusive with --baseline/"
        "--update-baseline."
    ),
)
@_no_refresh_option
@click.pass_context
def check(
    ctx: click.Context,
    use_baseline: bool,
    update_baseline: bool,
    strict: bool,
    apply_fixes: bool,
    no_refresh: bool,
) -> None:
    """Run semantic lint rules declared in [tool.pypeeker] of pyproject.toml.

    Exits non-zero if any violations are found. Output format matches
    ruff/mypy: 'path:line: [rule] message'. Stale index entries are
    re-indexed first unless --no-refresh is given.

    Low-confidence (heuristic/unknown) violations are hidden by default and
    summarized in a trailing note; --strict shows and counts them. Shown
    non-certain findings carry a trailing [tier] marker.

    With --baseline, only violations NOT covered by the recorded baseline are
    printed and counted toward the exit code, followed by a one-line summary.
    With --update-baseline, the current violations replace the baseline (and,
    when the born-private rule is enabled, the recorded public-symbol set is
    re-seeded from the current public surface). Violation identity in the
    baseline is line-independent, so unrelated edits that shift line numbers
    never re-fire baselined violations. Both baseline flows operate on the
    FULL violation set — the --strict display filter never changes what is
    recorded or compared.

    With --fix, violation-attached autofixes are planned against the current
    files and applied as one transaction; see the flag help for details.
    """
    from pypeeker.check import CheckEngine, load_config
    from pypeeker.check.baseline import (
        baseline_path,
        clear_symbol_baseline,
        delta,
        load_baseline,
        write_baseline,
    )

    if use_baseline and update_baseline:
        raise click.UsageError(
            "--baseline and --update-baseline are mutually exclusive: "
            "compare first, then update."
        )
    if apply_fixes and (use_baseline or update_baseline):
        raise click.UsageError(
            "--fix cannot be combined with --baseline/--update-baseline: "
            "fix first, then compare or re-record."
        )

    _refresh_index(ctx, no_refresh)
    store: IndexStore = ctx.obj["store"]
    root: Path = ctx.obj["root"]
    config = load_config(root)
    engine = CheckEngine(store, config)

    if update_baseline:
        from pypeeker.check.builtin.born_private import BORN_PRIVATE

        if BORN_PRIVATE in config.rules:
            # TASK-99 follow-up: --update-baseline also re-records the
            # accepted-public symbol set. Clearing the namespace makes the
            # born-private run below self-seed it (write_symbol_baseline)
            # with the current public surface.
            clear_symbol_baseline(baseline_path(root))

    violations = engine.run()

    if apply_fixes:
        _apply_check_fixes(ctx, engine, violations, strict)
        return

    if update_baseline:
        # Full set, never filtered: a baseline must not churn with --strict.
        counts = write_baseline(baseline_path(root), violations)
        click.echo(
            f"baseline updated: {sum(counts.values())} violation(s) recorded "
            f"in {baseline_path(root).relative_to(root)}"
        )
        return

    if use_baseline:
        # Delta over the full set (identities must match what was recorded);
        # only the *display* of new violations honors the confidence filter.
        baseline = load_baseline(baseline_path(root))
        new, fixed = delta(violations, baseline)
        shown, hidden = _split_by_confidence(new, strict)
        for v in shown:
            click.echo(str(v))
        click.echo(
            f"{sum(baseline.values())} baselined, {len(shown)} new, "
            f"{len(fixed)} fixed"
        )
        _echo_hidden_note(hidden)
        if shown:
            sys.exit(1)
        return

    shown, hidden = _split_by_confidence(violations, strict)
    for v in shown:
        click.echo(str(v))
    _echo_hidden_note(hidden)
    if shown:
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


def _required_str(entry: dict, key: str, where: str) -> str:
    """A non-empty string value for ``key``, or a ValueError naming the entry."""
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: missing or invalid '{key}' (expected a string)")
    return value


def _required_int(entry: dict, key: str, where: str) -> int:
    """An integer value for ``key``, or a ValueError naming the entry."""
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{where}: missing or invalid '{key}' (expected an integer)")
    return value


def _position(entry: dict, key: str, where: str) -> tuple[int, int]:
    """A 0-indexed ``(line, col)`` from a ``"line:col"`` string or ``[line, col]``."""
    value = entry.get(key)
    if isinstance(value, str):
        line, sep, col = value.partition(":")
        if sep:
            try:
                return int(line), int(col)
            except ValueError:
                pass
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        return value[0], value[1]
    raise ValueError(f"{where}: '{key}' must be a 'line:col' string or [line, col]")


def _expand_fix_rule(
    rule_name: str, base_id: str, store: IndexStore, root: Path
) -> list:
    """FixIntents for every certain-confidence autofix ``rule_name`` reports now.

    Runs the check engine with only ``rule_name`` enabled (the project's
    configured options for it still apply) and wraps the fix attached to each
    DECLARED-confidence violation as a deferred
    :class:`~pypeeker.refactor.intents.FixIntent` named ``{base_id}-{n}``.
    The eligibility filter (fix present + DECLARED confidence) deliberately
    mirrors :func:`_apply_check_fixes` — kept as light duplication because
    that path plans and applies immediately while this one defers planning to
    batch materialization, so the fix objects (not their edits) are what
    travel.
    """
    import dataclasses

    from pypeeker.check import CheckEngine, load_config
    from pypeeker.models.capabilities import Confidence
    from pypeeker.refactor.intents import FixIntent

    config = dataclasses.replace(load_config(root), rules=(rule_name,))
    violations = CheckEngine(store, config).run()
    fixes = [
        v.fix
        for v in violations
        if v.fix is not None and v.confidence is Confidence.DECLARED
    ]
    return [
        FixIntent(f"{base_id}-{n}", fix=fix) for n, fix in enumerate(fixes, start=1)
    ]


def _build_batch_intents(entries: object, store: IndexStore, root: Path) -> list:
    """Intent objects from a plan-batch intents file's parsed JSON.

    ``entries`` must be a list of objects, each with a ``kind`` of
    ``"rename"``, ``"inline-variable"``, ``"extract-variable"``,
    ``"extract-method"`` or ``"fix"`` plus that kind's parameters (mirroring
    the corresponding plan-* CLI arguments; ``fix`` takes ``rule`` and
    expands into one intent per certain-confidence autofix the rule reports,
    via :func:`_expand_fix_rule`). Optional ``id`` names the intent (default
    ``{kind}-{position}``); optional ``deps`` lists ids that must execute
    first — a dep naming a fix entry resolves to every intent the entry
    expanded into. Raises :class:`ValueError` with an entry-naming message on
    any malformed input.
    """
    import dataclasses

    from pypeeker.refactor.intents import (
        ExtractMethodIntent,
        ExtractVariableIntent,
        InlineVariableIntent,
        RenameIntent,
    )

    if not isinstance(entries, list):
        raise ValueError("intents file must contain a JSON list of intent objects")

    built: list[tuple[dict, list]] = []
    expansion: dict[str, list[str]] = {}
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"intent #{number} must be a JSON object")
        kind = entry.get("kind")
        where = f"intent #{number} ({kind!r})"
        entry_id = entry.get("id") or f"{kind}-{number}"
        if not isinstance(entry_id, str):
            raise ValueError(f"{where}: 'id' must be a string")
        if entry_id in expansion:
            raise ValueError(f"{where}: duplicate intent id '{entry_id}'")
        deps = entry.get("deps", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError(f"{where}: 'deps' must be a list of intent ids")

        if kind == "rename":
            intents = [
                RenameIntent(
                    entry_id,
                    _required_str(entry, "symbol_id", where),
                    _required_str(entry, "new_name", where),
                    include_file=bool(entry.get("include_file", False)),
                    include_exports=bool(entry.get("include_exports", False)),
                    include_receivers=bool(entry.get("include_receivers", False)),
                    keep_export=bool(entry.get("keep_export", False)),
                    allow_override_rename=bool(
                        entry.get("allow_override_rename", False)
                    ),
                )
            ]
        elif kind == "inline-variable":
            intents = [
                InlineVariableIntent(entry_id, _required_str(entry, "symbol_id", where))
            ]
        elif kind == "extract-variable":
            intents = [
                ExtractVariableIntent(
                    entry_id,
                    _required_str(entry, "file_path", where),
                    _position(entry, "start", where),
                    _position(entry, "end", where),
                    _required_str(entry, "new_name", where),
                )
            ]
        elif kind == "extract-method":
            intents = [
                ExtractMethodIntent(
                    entry_id,
                    _required_str(entry, "file_path", where),
                    _required_int(entry, "start_line", where),
                    _required_int(entry, "end_line", where),
                    _required_str(entry, "new_name", where),
                )
            ]
        elif kind == "fix":
            intents = _expand_fix_rule(
                _required_str(entry, "rule", where), entry_id, store, root
            )
        else:
            raise ValueError(
                f"{where}: unknown kind (expected rename, inline-variable, "
                "extract-variable, extract-method, or fix)"
            )
        expansion[entry_id] = [intent.intent_id for intent in intents]
        built.append((entry, intents))

    result: list = []
    for entry, intents in built:
        resolved: set[str] = set()
        for dep in entry.get("deps", []):
            resolved.update(expansion.get(dep, [dep]))
        for intent in intents:
            if resolved:
                intent = dataclasses.replace(intent, deps=frozenset(resolved))
            result.append(intent)
    return result


@main.command("plan-batch")
@click.argument("intents_file")
@click.option(
    "--policy",
    type=click.Choice(["skip", "abort"]),
    default="skip",
    show_default=True,
    help=(
        "What to do when an intent cannot execute: 'skip' drops it with a "
        "machine-readable reason and keeps going; 'abort' refuses the whole "
        "batch on the first drop."
    ),
)
@_no_refresh_option
@click.pass_context
def plan_batch(
    ctx: click.Context, intents_file: str, policy: str, no_refresh: bool
) -> None:
    """Plan a multi-intent batch as ONE flattened transaction.

    INTENTS_FILE is a JSON list of intent objects: {"kind": "rename" |
    "inline-variable" | "extract-variable" | "extract-method" | "fix", plus
    that kind's parameters (mirroring the matching plan-* command's
    arguments; "fix" takes "rule" and expands into every certain-confidence
    autofix that rule reports), optional "id" and "deps": [ids]}.

    The intents are scheduled, simulated against a temporary mirror of the
    project (each intent re-plans against the state earlier intents left, so
    offsets never go stale), and the mirror's net change is flattened into a
    single transaction applied with 'apply' and reverted with 'rollback'.
    Prints {tx_id, executed, dropped, files_affected, edit_count}; tx_id is
    null when the batch was a net no-op. Exits 1 when every intent dropped,
    when --policy abort aborted, or on malformed input ({"error": ...}).
    Stale index entries are re-indexed first unless --no-refresh is given.
    """
    import shutil
    import tempfile

    from pypeeker.refactor.batch import (
        BatchAborted,
        BatchPolicy,
        FlattenError,
        ScheduleError,
        flatten_batch,
        run_batch,
    )

    def _fail(payload: dict) -> None:
        """Print an error payload as JSON and exit 1."""
        click.echo(json.dumps(payload, indent=2))
        sys.exit(1)

    def _dropped(d) -> dict:
        """JSON shape for one dropped intent."""
        return {
            "id": d.intent.intent_id,
            "reason": d.reason.value,
            "detail": d.detail,
        }

    _refresh_index(ctx, no_refresh)
    store: IndexStore = ctx.obj["store"]
    root: Path = ctx.obj["root"]
    try:
        entries = json.loads(Path(intents_file).read_text())
    except OSError as e:
        _fail({"error": f"cannot read intents file: {e}"})
    except json.JSONDecodeError as e:
        _fail({"error": f"intents file is not valid JSON: {e}"})
    try:
        intents = _build_batch_intents(entries, store, root)
    except ValueError as e:
        _fail({"error": str(e)})
    if not intents:
        _fail({"error": "intents file contains no executable intents"})

    batch_policy = (
        BatchPolicy.ALL_OR_NOTHING if policy == "abort" else BatchPolicy.SKIP_AND_REPORT
    )
    work_dir = Path(tempfile.mkdtemp(prefix="pypeeker-plan-batch-"))
    try:
        result = run_batch(intents, store, policy=batch_policy, work_dir=work_dir)
        header, edits = flatten_batch(result, store)
    except BatchAborted as e:
        _fail({"error": str(e), "dropped": [_dropped(d) for d in e.dropped]})
    except (ScheduleError, FlattenError) as e:
        _fail({"error": str(e)})
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    dropped = [_dropped(d) for d in result.dropped]
    if not result.executed:
        _fail({"error": "all intents were dropped", "dropped": dropped})
    tx_id = None
    if edits:
        ctx.obj["transaction_store"].save(header, edits)
        tx_id = header.tx_id
    click.echo(
        json.dumps(
            {
                "tx_id": tx_id,
                "executed": [
                    {"id": e.intent.intent_id, "kind": e.intent.kind}
                    for e in result.executed
                ],
                "dropped": dropped,
                "files_affected": sorted({edit.file for edit in edits}),
                "edit_count": len(edits),
            },
            indent=2,
        )
    )


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
@click.argument("symbol_id")
@click.option(
    "--keep-export",
    is_flag=True,
    default=False,
    help=(
        "Demote the definition but keep the public package export name "
        "(rewrites the __init__ re-export to '_name as name')."
    ),
)
@_no_refresh_option
@click.pass_context
def demote(
    ctx: click.Context, symbol_id: str, keep_export: bool, no_refresh: bool
) -> None:
    """Plan demoting a public symbol to non-public (name -> _name).

    SYMBOL_ID is the symbol to demote (name, partial ID, or full ID). Plans
    a rename of the symbol and every reference to the underscore-prefixed
    name as a transaction applied with the 'apply' command. A barrel-exported
    symbol has its __init__ re-export (and consumers) rewritten too, with a
    warning in the output; --keep-export instead aliases the re-export so
    the package keeps the public name. Stale index entries are re-indexed
    first unless --no-refresh is given.

    Refused (JSON {"error", "code"}, exit 1) when: the name is already
    underscore-prefixed (already-private); the symbol is barrel-exported
    under a public root in library mode (protected-public-api); or a rename
    precondition fails — e.g. '_name' already exists in the scope, or the
    method overrides / is overridden by another method (rename-refused).
    """
    from pypeeker.refactor.visibility_ops import VisibilityOpError, VisibilityPlanner

    _refresh_index(ctx, no_refresh)
    planner = VisibilityPlanner(ctx.obj["store"], ctx.obj["transaction_store"])
    try:
        result = planner.plan_demote(symbol_id, keep_export=keep_export)
    except VisibilityOpError as e:
        click.echo(json.dumps({"error": str(e), "code": e.code}))
        sys.exit(1)
    output = to_dict(result.summary)
    if result.warnings:
        output["warnings"] = result.warnings
    click.echo(json.dumps(output, indent=2))


@main.command()
@click.argument("symbol_id")
@click.option(
    "--add-export",
    "add_export",
    metavar="PKG",
    default=None,
    help=(
        "Also export the promoted name from this package (dotted path): "
        "inserts 'from .mod import Name' into PKG/__init__.py and prepends "
        "the name to __all__ when one exists."
    ),
)
@_no_refresh_option
@click.pass_context
def promote(
    ctx: click.Context, symbol_id: str, add_export: str | None, no_refresh: bool
) -> None:
    """Plan promoting a non-public symbol to public (_name -> name).

    SYMBOL_ID is the symbol to promote (name, partial ID, or full ID). The
    new name strips exactly one leading underscore; the symbol and every
    reference are renamed as a transaction applied with the 'apply' command.
    With --add-export PKG the same transaction also adds an import of the
    new name to PKG/__init__.py (and a __all__ entry when __all__ exists).
    Stale index entries are re-indexed first unless --no-refresh is given.

    Refused (JSON {"error", "code"}, exit 1) when: the name has no leading
    underscore (already-public); the name is a dunder (dunder); the
    --add-export package has no indexed __init__.py or already binds the
    name (export-target); or a rename precondition fails — e.g. the public
    name already exists in the scope, or the method overrides / is
    overridden by another method (rename-refused).
    """
    from pypeeker.refactor.visibility_ops import VisibilityOpError, VisibilityPlanner

    _refresh_index(ctx, no_refresh)
    planner = VisibilityPlanner(ctx.obj["store"], ctx.obj["transaction_store"])
    try:
        result = planner.plan_promote(symbol_id, add_export=add_export)
    except VisibilityOpError as e:
        click.echo(json.dumps({"error": str(e), "code": e.code}))
        sys.exit(1)
    output = to_dict(result.summary)
    if result.warnings:
        output["warnings"] = result.warnings
    click.echo(json.dumps(output, indent=2))


# The demotion-feeding rules the privatize command may run. Kept as literals
# so the CLI module stays lazy about importing the check rule machinery; a
# test asserts this tuple equals pypeeker.check.demotion.DEMOTION_RULES.
_PRIVATIZE_RULES = (
    "over-exposed-module-symbol",
    "unused-public-symbol",
    "test-only-production-code",
)


@main.command()
@click.option(
    "--rule",
    "rules",
    multiple=True,
    type=click.Choice(_PRIVATIZE_RULES),
    help=(
        "Demotion-feeding rule to run (repeatable). Default: all of "
        f"{', '.join(_PRIVATIZE_RULES)}. The project's configured options "
        "for each rule (and [tool.pypeeker.visibility]) still apply."
    ),
)
@click.option(
    "--apply",
    "apply_plan",
    is_flag=True,
    default=False,
    help=(
        "Apply the planned transaction immediately (revert with "
        "'rollback <tx_id>'). Without this flag the transaction stays "
        "PENDING for inspection via 'transactions show <tx_id>' and a "
        "later 'apply <tx_id>'."
    ),
)
@click.option(
    "--include-heuristic",
    is_flag=True,
    default=False,
    help=(
        "Also demote symbols nominated by heuristic-confidence findings "
        "(dynamic access nearby may consume them invisibly). By default "
        "those are skipped with reason 'heuristic-confidence'."
    ),
)
@_no_refresh_option
@click.pass_context
def privatize(
    ctx: click.Context,
    rules: tuple[str, ...],
    apply_plan: bool,
    include_heuristic: bool,
    no_refresh: bool,
) -> None:
    """Plan a mass demotion (name -> _name) driven by check findings.

    Runs the selected demotion-feeding rules (default: all three) with the
    project's configured options, extracts the nominated symbols from the
    findings, and plans ONE flattened batch demotion transaction via the
    batch machinery — collisions, ordering, and barrel/__all__ rewrites are
    handled exactly like 'plan-batch'. The real tree is never written unless
    --apply is given; preview the pending transaction with 'transactions
    show <tx_id>' and execute it with 'apply <tx_id>'.

    Prints {tx_id, executed, dropped, skipped, warnings, files_affected,
    edit_count}: 'skipped' lists pre-filter exclusions with machine-readable
    reasons (already-private, hierarchy-unsafe, name collisions, library-mode
    protected API, heuristic confidence, ...), 'dropped' lists batch-execution
    drops, and 'warnings' notes public-surface changes (rewritten barrel
    exports). With --apply the report gains an 'applied' key with the apply
    result. Exits 1 when nothing was plannable (no transaction was created).
    Stale index entries are re-indexed first unless --no-refresh is given.
    """
    import dataclasses

    from pypeeker.check import CheckEngine, load_config
    from pypeeker.check.demotion import demote_entry
    from pypeeker.refactor.applier import ApplyError, TransactionApplier
    from pypeeker.refactor.privatize import plan_privatize

    _refresh_index(ctx, no_refresh)
    store: IndexStore = ctx.obj["store"]
    transaction_store: TransactionStore = ctx.obj["transaction_store"]
    root: Path = ctx.obj["root"]

    selected = tuple(dict.fromkeys(rules)) or _PRIVATIZE_RULES
    base = load_config(root)
    # load_config injects the [tool.pypeeker.visibility] table only into the
    # rules the pyproject enables; the selected rules here may not be among
    # them, so inject the parsed config ourselves (setdefault keeps any
    # explicit per-rule override and the injected raw table intact).
    rule_options = {name: dict(opts) for name, opts in base.rule_options.items()}
    for name in selected:
        rule_options.setdefault(name, {}).setdefault("visibility", base.visibility)
    config = dataclasses.replace(base, rules=selected, rule_options=rule_options)

    violations = CheckEngine(store, config).run()
    entries = []
    for violation in violations:
        entry = demote_entry(violation)
        if entry is not None:
            entries.append(entry)
    outcome = plan_privatize(
        store,
        transaction_store,
        entries,
        skip_heuristic=not include_heuristic,
    )
    summary = outcome.summary
    output = {
        "tx_id": summary.tx_id if summary else None,
        "executed": [
            {"id": e.intent_id, "symbol_id": e.symbol_id, "new_name": e.new_name}
            for e in outcome.executed
        ],
        "dropped": [
            {"id": d.intent.intent_id, "reason": d.reason.value, "detail": d.detail}
            for d in outcome.dropped
        ],
        "skipped": [
            {"symbol_id": s.symbol_id, "reason": s.reason, "detail": s.detail}
            for s in outcome.skipped
        ],
        "warnings": outcome.warnings,
        "files_affected": list(summary.files_affected) if summary else [],
        "edit_count": summary.edit_count if summary else 0,
    }
    if summary is None:
        click.echo(json.dumps(output, indent=2))
        sys.exit(1)
    if apply_plan:
        try:
            output["applied"] = TransactionApplier(store, transaction_store).apply(
                summary.tx_id
            )
        except ApplyError as e:
            output["error"] = str(e)
            click.echo(json.dumps(output, indent=2))
            sys.exit(1)
    click.echo(json.dumps(output, indent=2))


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
