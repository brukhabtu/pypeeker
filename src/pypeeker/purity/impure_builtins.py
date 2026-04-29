"""Hardcoded denylists of known-impure names.

Pypeeker stores unresolved bare calls (e.g. ``print``) as ``symbol_id='print'``
and unresolved attribute calls (e.g. ``os.system``) as
``symbol_id='<unresolved>.system'`` — the base (``os``) is dropped. So we match
two ways:

* IMPURE_BUILTINS: exact bare-name match (``print``, ``open``, ...).
* IMPURE_ATTRIBUTE_NAMES: tail-name match for attribute calls
  (``<unresolved>.system`` → tail is ``system``).

The attribute-name denylist over-matches by design (any ``obj.write()`` is
flagged regardless of ``obj`` type) — this is acceptable for a heuristic.
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

# Attribute/method names commonly indicating IO, mutation, or non-determinism.
# Matched as the tail of '<unresolved>.<name>' calls.
IMPURE_ATTRIBUTE_NAMES: frozenset[str] = frozenset({
    # filesystem / process (os, subprocess, pathlib)
    "system",
    "popen",
    "spawn",
    "remove",
    "unlink",
    "rmdir",
    "mkdir",
    "makedirs",
    "rename",
    "replace",
    "chdir",
    "chmod",
    "chown",
    "touch",
    # i/o
    "write",
    "writelines",
    "read",  # file reads are still impure (depend on filesystem state)
    "readline",
    "readlines",
    "flush",
    "close",
    "send",
    "recv",
    "connect",
    "bind",
    "listen",
    "accept",
    # mutation patterns on common collections (catches arg.append() etc.)
    "append",
    "extend",
    "insert",
    "pop",
    "clear",
    "update",
    "setdefault",
    "sort",
    "reverse",
    "add",
    "discard",
    "popitem",
    # non-determinism
    "now",
    "today",
    "utcnow",
    "time",
    "monotonic",
    "perf_counter",
    "random",
    "randint",
    "choice",
    "shuffle",
    "uniform",
    "sample",
    # logging / warnings
    "log",
    "info",
    "debug",
    "warning",
    "error",
    "critical",
    "warn",
})


def is_impure_builtin(name: str) -> bool:
    """True if `name` matches a known-impure builtin exactly."""
    return name in IMPURE_BUILTINS


def is_impure_attribute(name: str) -> bool:
    """True if `name` is a method/attribute name commonly indicating impurity."""
    return name in IMPURE_ATTRIBUTE_NAMES
