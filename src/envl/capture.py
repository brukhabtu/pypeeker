"""Running a command and recording exactly what it printed.

:func:`run_command` **always executes**. There is deliberately no cached-result
path here and none may be added: an agent edits files between runs, so serving
a previous run's output as though it were live is a silent-wrong-data
correctness bug. The blob cache written downstream is a *snapshot store*,
labelled with when it was captured — wanting fresh data means re-running the
command, which is correct behaviour rather than a cache miss.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Capture:
    """One command execution: what ran, where, what it printed, and when.

    ``output`` is stdout and stderr concatenated, in that order. Combining them
    matches what a shell tool result actually contains, which is what the
    envelope has to summarize.
    """

    argv: tuple[str, ...]
    command: str
    cwd: str
    exit_code: int
    output: str
    captured_at: str


def run_command(argv: Sequence[str], *, cwd: Path | None = None) -> Capture:
    """Execute ``argv`` and return its combined output as a :class:`Capture`.

    Always runs the command; never consults any cache. A non-zero exit is
    recorded, not raised — the envelope reports ``exit_code`` and the CLI
    propagates it.

    Decoding is deliberately lossy (``errors="replace"``). A wrapper has to be
    transparent: a command emitting one stray non-UTF-8 byte — a ``git diff``
    over a latin-1 file, a ``grep`` across mixed encodings, a test dumping raw
    bytes — must not turn into a traceback that destroys the output and
    substitutes exit 1 for the command's real exit code. ``surrogateescape``
    would only move the crash downstream, where the payload is encoded for the
    blob; ``replace`` produces a str that survives every later encode.
    """
    directory = Path.cwd() if cwd is None else cwd
    completed = subprocess.run(  # noqa: S603 - the caller's command is the point
        list(argv),
        capture_output=True,
        check=False,
        cwd=str(directory),
        encoding="utf-8",
        errors="replace",
    )
    return Capture(
        argv=tuple(argv),
        command=shlex.join(argv),
        cwd=str(directory),
        exit_code=completed.returncode,
        output=(completed.stdout or "") + (completed.stderr or ""),
        captured_at=_utc_now(),
    )


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
