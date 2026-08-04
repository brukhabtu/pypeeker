#!/usr/bin/env python3
"""PostToolUse(Bash) hook: replace fat, allowlisted command output with an envelope.

Wired in ``.claude/settings.json`` under ``PostToolUse`` with ``matcher: Bash``.
On stdin it receives the hook payload; on stdout it emits::

    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "updatedToolOutput": {"stdout": "<envelope JSON>",
                                                  "stderr": "", ...}}}

``updatedToolOutput`` *replaces the tool result the model sees*, so this script
rewrites output for every call that qualifies. That blast radius, not token
savings, is the dominant constraint on everything below.

The replacement is an **object in the Bash tool's own output shape**, not a
bare string. The harness hands the value to that tool's result mapper, which
destructures ``{interrupted, stdout, stderr, isImage, ...}`` and calls
``.trim()`` on ``stderr``; a string makes ``stderr`` undefined, the mapper
throws, and the harness *discards the replacement*, keeps the original output
and writes a ``hook_error_during_execution`` notice into the transcript. So the
envelope travels in ``stdout``, with the rest of the original response carried
through unchanged.

Five properties hold it in check:

**Allowlist, not filter.** The command registry in ``.claude/hooks/envelope.yaml``
is read as an allowlist: a command is enveloped only when it matches a rule, so
every unregistered command passes through byte-identical. The allowlist is git
and nothing else, and pytest is excluded explicitly rather than by omission —
the evidence for both is in ``.claude/workflows/ENVELOPE-COUNTERFACTUAL.md`` and
is summarised at each rule in the YAML.

**Anchored on the program, not on the string.** A rule allows a command only
when it matches the *start of a shell segment* — the executed program and its
arguments — so ``grep -rn ' git ' CLAUDE.md`` is not a git command and is not
enveloped, while ``cd /repo && git diff | head -200`` is. See
:func:`executed_segments` and :func:`allowed_rule`. Exclusions are matched
against the whole command string as well, because declining is always the safe
direction.

**One config.** Threshold, kill switch and registry come from ``envl``'s own
loader and schema (``envl.load_config``), not from a parallel set of knobs. The
hook adds no configuration surface of its own beyond the location of that file
and one env kill switch.

**Fail-open, always.** Every failure path — malformed payload, unreadable
config, missing ``envl``, an unwritable blob cache, a bug here — emits *nothing*
and exits 0, which leaves the original tool output untouched. Emitting nothing
is the safe default rather than a degraded one, so there is no error path that
can corrupt a tool result. A hook that exceeds its ``timeout`` in settings.json
is discarded by the harness, which lands on the same untouched output.

**Never the failing path.** Only a response that has the *shape of a success*
is enveloped. Measured against this harness's own transcripts: a successful
Bash call reports ``tool_response`` as a mapping (``stdout``, ``stderr``,
``interrupted``, ``isImage``, …), and a failing one reports it as a plain
**string** beginning ``Error: Exit code N``. So a non-mapping response is read
as a failure and passes through whole — that is the shape check, not a guess at
a numeric exit code, which no observed success payload carries at all. Failure
output is what a human or an agent reads in full, and it is the case where this
hook cannot know the real exit code; rather than stamp an envelope with a
fabricated ``exit_code: 0``, it declines. Every enveloped capture is therefore a
successful one, and its ``exit_code`` is honest.

The full output is always written to ``envl``'s content-addressed blob cache
*before* the envelope naming it is emitted, so the drill-in property survives:
``summary.blob.path`` points at the complete, immutable bytes.

Run standalone for a smoke test::

    echo '{"tool_name":"Bash","tool_input":{"command":"git diff"},
           "tool_response":{"stdout":"..."}}' \\
      | uv run --no-sync python .claude/hooks/envelope-post-tool-use.py
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

_HOOKS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HOOKS_DIR.parent.parent

#: Committed hook config. Deliberately not ``<repo>/.envl.yaml``: that path is on
#: ``load_config``'s discovery list and would change every ``envl`` run whose cwd
#: is this repo.
CONFIG_PATH = _HOOKS_DIR / "envelope.yaml"

#: Hook-only kill switch, for a session where the envelope is in the way. The
#: shared switches (``enabled:`` in the YAML, ``$ENVL_ENABLED``) are honoured too,
#: via ``load_config``; this one disables the hook while leaving the CLI working.
KILL_SWITCH_ENV = "ENVL_HOOK_ENABLED"

#: Mirrors ``envl.config._FALSE_VALUES``. Copied rather than imported: the kill
#: switch has to be readable before — and independently of — the ``envl`` import,
#: so that "envl is broken" and "the hook is switched off" stay separate answers.
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

_FAILURE_FLAGS = ("is_error", "isError", "interrupted")
_EXIT_CODE_KEYS = ("exit_code", "exitCode", "returnCode")
_OUTPUT_KEYS = ("output", "content", "result")

#: Shell metacharacters that end one executed segment and start the next. ``>``
#: and ``<`` are absent on purpose: a redirect continues the same command.
_SEGMENT_SEPARATORS = frozenset("&|;\n")


def hook_output(payload: Mapping[str, Any]) -> str:
    """Return what this hook should print for ``payload``.

    An empty string means *pass through*: the harness leaves the tool result
    exactly as the command produced it. Any internal failure is converted to
    that empty string here, which is the single place the fail-open guarantee
    is implemented.
    """
    try:
        return _hook_output(payload)
    except Exception:  # noqa: BLE001 - fail-open is the whole point
        return ""


def _hook_output(payload: Mapping[str, Any]) -> str:
    if payload.get("tool_name") != "Bash":
        return ""
    if not hook_enabled():
        return ""
    command = _command(payload)
    if not command:
        return ""
    response = payload.get("tool_response")
    if is_failure(response):
        # See the module docstring: the failing path is the one a reader needs
        # whole, and the one where exit_code cannot be established.
        return ""
    output = response_output(response)
    if not output:
        return ""

    from envl import (
        BlobCache,
        Capture,
        build_envelope,
        detect_format,
        envelope_json,
        load_config,
        should_envelope,
    )

    config = load_config(_config_path())
    if allowed_rule(command, config) is None:
        # Unregistered, explicitly excluded, or named only in an argument.
        # The allowlist is the point.
        return ""
    capture = Capture(
        # The Bash tool runs a shell string, so there is no real argv to record;
        # this is the shape that would re-run it, and it only reaches the cache
        # manifest. `command` is what the registry matched and what the envelope
        # echoes.
        argv=("bash", "-c", command),
        command=command,
        cwd=str(payload.get("cwd") or Path.cwd()),
        exit_code=0,
        output=output,
        captured_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if not should_envelope(capture, config):
        # Below `threshold_bytes`, or the shared kill switch is off. Library
        # predicate rather than a reimplemented byte comparison.
        return ""
    fmt = detect_format(command, output, declared=config.settings_for(command).format)
    cache = BlobCache(config.cache_dir)
    # Store first, emit second: an envelope is only ever handed out once the
    # bytes it points at are on disk, so `blob.path` is never a promise.
    blob = cache.store(capture, fmt)
    cache.prune(config.cache_max_bytes, config.cache_ttl_days, keep=blob.digest)
    envelope = build_envelope(capture, config, blob)
    replacement = {
        # The harness feeds this to the Bash tool's result mapper, which
        # destructures {interrupted, stdout, stderr, isImage, ...}; the two
        # defaults below only fill in for a response that omitted them, so the
        # mapper never sees an undefined field it calls a method on. Everything
        # else in the original response is carried through unchanged: the
        # envelope replaces the *output*, not the result.
        "interrupted": False,
        "isImage": False,
        **dict(response),
        "stdout": envelope_json(envelope),
        "stderr": "",
    }
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": replacement,
            }
        }
    )


def hook_enabled() -> bool:
    """Report whether the hook-only kill switch leaves this hook running."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSE_VALUES


def executed_segments(command: str) -> tuple[str, ...]:
    """Split a shell command into the segments that each run one program.

    Splits on ``&``, ``|``, ``;`` and newlines *outside* quotes, so the pieces
    of ``cd /repo && git diff | head -200`` are ``cd /repo``, ``git diff`` and
    ``head -200``, while ``grep -rn "a && git b" f`` stays one segment whose
    program is ``grep``. Quote tracking is what makes the second case work, and
    it is the whole reason this is not a ``str.split``.

    This is not a shell parser and does not try to be one: a command
    substitution stays inside its segment (``echo $(git log)`` runs ``echo``),
    and a leading ``VAR=value`` assignment is left in place, which makes the
    segment fail to match rather than match something else. Both err toward
    *not* enveloping, which is the direction that costs only tokens.
    """
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == "\\" and index + 1 < len(command):
            current.append(command[index : index + 2])
            index += 2
            continue
        elif char in _SEGMENT_SEPARATORS:
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return tuple(stripped for segment in segments if (stripped := segment.strip()))


def allowed_rule(command: str, config: Any) -> Any:
    """Return the registry rule that allows ``command``, or ``None``.

    Two passes, in this order:

    1. *Exclusion.* A rule with ``envelope: false`` matching the whole command
       string — or any one segment of it — declines outright, so
       ``git diff && uv run pytest -q`` is a pytest command and not a git one.
    2. *Allow.* A segment is allowed only by a rule whose pattern is
       **anchored**, i.e. does not begin with ``*``. An anchored pattern can
       only match from the start of a segment, which is the executed program —
       so a rule can never be satisfied by the string ``git`` appearing in an
       argument. A leading-wildcard rule is therefore an exclusion-only rule
       here; that constraint is what keeps a future widening of the registry
       from quietly re-admitting a family the counterfactual excluded.
    """
    segments = executed_segments(command)
    for candidate in (command, *segments):
        rule = config.matching_rule(candidate)
        if rule is not None and rule.envelope is False:
            return None
    for segment in segments:
        rule = config.matching_rule(segment)
        if rule is not None and not rule.match.startswith("*"):
            return rule
    return None


def is_failure(response: Any) -> bool:
    """Report whether ``response`` carries any signal that the command failed.

    A response that is not a mapping is read as a failure: measured across this
    harness's transcripts, every successful Bash result is a mapping and every
    failed one is a plain string beginning ``Error: Exit code N``. Refusing the
    shape is stronger than looking for a numeric exit code, which no observed
    success payload carries.

    The mapping checks below stay deliberately generous about spelling — the
    payload's exact shape is the harness's to change — because each recognised
    signal only ever makes the hook *decline* to touch the output.
    """
    if not isinstance(response, Mapping):
        return True
    if any(bool(response.get(flag)) for flag in _FAILURE_FLAGS):
        return True
    for key in _EXIT_CODE_KEYS:
        value = response.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and int(value) != 0:
            return True
    return False


def response_output(response: Any) -> str:
    """Extract the command's combined output from a tool response.

    ``stdout`` then ``stderr``, concatenated in that order — the same
    convention :class:`envl.Capture` documents, so the enveloped payload is the
    payload the model would otherwise have read. Returns ``""`` when nothing
    recognisable is present, which the caller treats as pass-through.

    A bare string is *not* accepted, deliberately: in this harness that is the
    failure shape (``Error: Exit code N…``), and accepting it here is what
    would let a failed command past :func:`is_failure` and into an envelope
    stamped ``exit_code: 0``.
    """
    if not isinstance(response, Mapping):
        return ""
    streams = "".join(
        value
        for key in ("stdout", "stderr")
        if isinstance(value := response.get(key), str)
    )
    if streams:
        return streams
    for key in _OUTPUT_KEYS:
        value = response.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _command(payload: Mapping[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return ""
    command = tool_input.get("command")
    return command.strip() if isinstance(command, str) else ""


def _config_path() -> Path | None:
    """Return the config file to load, or ``None`` to use ``envl``'s discovery.

    ``$ENVL_CONFIG`` wins: someone who pointed ``envl`` at a config meant it for
    the hook too, and ``load_config`` already honours that variable first.
    """
    if os.environ.get("ENVL_CONFIG"):
        return None
    return CONFIG_PATH if CONFIG_PATH.is_file() else None


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Read a hook payload, write the replacement (or nothing), and exit 0.

    The exit status is always 0. A non-zero exit from a PostToolUse hook is a
    signal to the harness, not a private failure, and this hook has no opinion
    worth blocking a tool call over.
    """
    source = sys.stdin if stdin is None else stdin
    sink = sys.stdout if stdout is None else stdout
    try:
        payload = json.load(source)
    except Exception:  # noqa: BLE001 - malformed input is a pass-through, not a crash
        return 0
    if not isinstance(payload, Mapping):
        return 0
    emitted = hook_output(payload)
    if emitted:
        sink.write(emitted + "\n")
    return 0


if __name__ == "__main__":
    if str(_REPO_ROOT / "src") not in sys.path:
        # Only when run as a script: under `uv run` the project is already
        # importable, and this keeps the hook working if someone invokes it with
        # a bare python3 that has PyYAML but no editable install.
        sys.path.append(str(_REPO_ROOT / "src"))
    raise SystemExit(main())
