"""Hardcoded denylists used by the purity check.

These are *purity-specific policy*, not intrinsic semantic facts. A future
DeterminismCheck might subset this to only the non-deterministic entries
(``random``, ``time``, ``now``, ...); a SideEffectCheck might subset to
only the IO entries.

Pypeeker stores unresolved bare calls (e.g. ``print``) as ``symbol_id='print'``
and unresolved attribute calls (e.g. ``os.system``) as
``symbol_id='<unresolved>.system'`` — the receiver is dropped. So we match
two ways:

* IMPURE_BUILTINS: exact bare-name match.
* IMPURE_ATTRIBUTE_NAMES: tail-name match for attribute calls.

The attribute-name denylist over-matches by design (any ``obj.write()`` is
flagged regardless of ``obj`` type) — acceptable for a heuristic.
"""

IMPURE_BUILTINS: frozenset[str] = frozenset({
    "print",
    "open",
    "input",
    "exec",
    "eval",
    "compile",
    "breakpoint",
    "exit",
    "quit",
    "help",
})

IMPURE_ATTRIBUTE_NAMES: frozenset[str] = frozenset({
    # filesystem / process (os, subprocess, pathlib)
    "system", "popen", "spawn", "remove", "unlink", "rmdir", "mkdir",
    "makedirs", "rename", "replace", "chdir", "chmod", "chown", "touch",
    # i/o
    "write", "writelines", "read", "readline", "readlines",
    "flush", "close", "send", "recv", "connect", "bind", "listen", "accept",
    # mutation patterns on common collections
    "append", "extend", "insert", "pop", "clear", "update", "setdefault",
    "sort", "reverse", "add", "discard", "popitem",
    # non-determinism
    "now", "today", "utcnow", "time", "monotonic", "perf_counter",
    "random", "randint", "choice", "shuffle", "uniform", "sample",
    # logging / warnings
    "log", "info", "debug", "warning", "error", "critical", "warn",
})
