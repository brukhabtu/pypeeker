"""The ``envl`` command: run a command, cache its output, print the envelope.

Usage is ``envl [--config PATH] [--format FMT] -- <command>...``. Everything
after ``--`` is handed to the wrapped command untouched, and ``envl`` exits
with that command's exit code so an agent's ``&&`` chains and ``$?`` checks
keep working.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import click

from envl.cache import BlobCache
from envl.capture import run_command
from envl.config import CommandRule, load_config
from envl.envelope import build_envelope, envelope_json, should_envelope
from envl.formats import detect_format


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False}
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Explicit envl YAML config (overrides discovery).",
)
@click.option(
    "--format",
    "format_override",
    default=None,
    help="Force a format adapter instead of registry lookup and sniffing.",
)
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def main(
    config_path: Path | None, format_override: str | None, command: tuple[str, ...]
) -> None:
    """Run COMMAND, cache its full output, and emit a compact JSON envelope."""
    if not command:
        raise click.UsageError("no command given; usage: envl -- <command>")
    try:
        config = load_config(config_path)
    except ValueError as error:
        # A rejected config (e.g. a repo-local cache dir) must fail loudly here
        # rather than as a traceback: the wrapped command has not run yet, so
        # nothing is lost by refusing.
        raise click.ClickException(str(error)) from error
    if format_override:
        # First match wins, so a prepended catch-all rule is the whole override
        # mechanism — the summarizer sees the forced format too, not just the
        # envelope's `format` field.
        forced = CommandRule(match="*", format=format_override)
        config = replace(config, commands=(forced, *config.commands))
    capture = run_command(list(command))
    if not should_envelope(capture, config):
        click.echo(capture.output, nl=False)
        sys.exit(capture.exit_code)
    rule = config.settings_for(capture.command)
    fmt = detect_format(capture.command, capture.output, declared=rule.format)
    cache = BlobCache(config.cache_dir)
    blob = cache.store(capture, fmt)
    # `keep=` so the prune that runs between storing and printing can never
    # delete the blob this envelope is about to point at.
    cache.prune(config.cache_max_bytes, config.cache_ttl_days, keep=blob.digest)
    click.echo(envelope_json(build_envelope(capture, config, blob)))
    sys.exit(capture.exit_code)
