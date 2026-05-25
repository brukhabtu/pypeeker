"""Purity analysis: does this function have observable side effects?

The single public function returns an :class:`Observations` instance with
the impurity it found, or ``None`` if the function can't be analyzed:

    None              — couldn't analyze (not found, not a function, ...)
    Observations()    — pure (no impurity found, falsy)
    Observations(...) — impure with these observations (truthy)

The bool / iter / len semantics on the result follow the standard Python
container convention (empty=falsy). ``if is_pure(x):`` reads as "found
impurity" — the function name describes the question being asked, the
container's truthiness describes the result. Use ``not is_pure(x)`` plus
a ``None`` check for the explicit pure-predicate.

Once PEP 661 (Sentinel Values) lands — targeted for Python 3.15 — we can
introduce a named ``UNKNOWN`` sentinel so call sites read more directly
(``result is UNKNOWN`` vs ``result is None``).

The analysis is heuristic. An empty :class:`Observations` means "no
impurity was found by the configured policy," not "provably pure."
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pypeeker.analysis.calls import (
    AttributeMethodCall,
    BareCall,
    ModuleCall,
    ReceiverKind,
    attribute_method_calls,
    bare_calls,
    module_calls,
)
from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.graph import (
    TransitiveImpureCall,
    call_graph,
    functions_reachable_from,
)
from pypeeker.analysis.observations import Observations
from pypeeker.analysis.writes import (
    AttributeWrite,
    OuterScopeWrite,
    attribute_writes,
    outer_scope_writes,
)
from pypeeker.storage import IndexStore

# An impurity observation: any of the typed facts we collect, plus the
# transitive call link surfaced by the call-graph variant.
Observation = (
    OuterScopeWrite
    | AttributeWrite
    | BareCall
    | ModuleCall
    | AttributeMethodCall
    | TransitiveImpureCall
)


# --- Policy: which names this analysis treats as impure ----------------------

IMPURE_BUILTINS: frozenset[str] = frozenset({
    "print", "open", "input", "exec", "eval", "compile",
    "breakpoint", "exit", "quit", "help",
})

# Methods that are impure on any receiver (file / network / system I/O is
# I/O regardless of who owns the handle / path / socket).
#
# Names omitted intentionally because they over-match outside their stdlib
# context — caught by MODULE_IMPURE_NAMES instead, or left to the typed
# receiver pass:
#   bind, accept, listen, connect, shutdown — overloaded
#   replace — str.replace is the dominant Python idiom; pathlib Path.replace
#       is caught by MODULE_IMPURE_NAMES
#   remove — moved to COLLECTION_MUTATION_NAMES (list/set are pure-local;
#       os.remove / Path.unlink caught via MODULE_IMPURE_NAMES)
IO_METHOD_NAMES: frozenset[str] = frozenset({
    "write_text", "write_bytes", "read_text", "read_bytes",
    "unlink", "rmdir", "touch", "chmod",
    "write", "writelines", "read", "readline", "readlines",
    "flush", "truncate",
    "recv", "recvfrom",
    "system", "popen", "spawn",
    "mkdir", "makedirs", "rename", "chown", "symlink",
})

# Methods that mutate their receiver but are pure-local — only impure when
# the receiver is a parameter (or unknown receiver, conservatively).
COLLECTION_MUTATION_NAMES: frozenset[str] = frozenset({
    "append", "extend", "insert", "pop", "clear", "update", "setdefault",
    "sort", "reverse", "add", "discard", "popitem", "remove",
})

MODULE_IMPURE_NAMES: frozenset[str] = frozenset({
    # os: process / filesystem
    "os.system", "os.popen", "os.spawn", "os.spawnl", "os.spawnv",
    "os.exec", "os.execl", "os.execv", "os.execve",
    "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
    "os.mkdir", "os.makedirs", "os.rename", "os.renames", "os.replace",
    "os.chdir", "os.chmod", "os.chown", "os.symlink", "os.link",
    "os.truncate", "os.utime", "os.write", "os.read",
    "os.kill", "os.killpg", "os.fork", "os.wait", "os.waitpid",
    "os.environ.pop", "os.environ.update",
    "os.path.exists", "os.path.isfile", "os.path.isdir",
    # subprocess
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen", "subprocess.getoutput",
    "subprocess.getstatusoutput",
    # shutil
    "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree",
    "shutil.move", "shutil.rmtree", "shutil.chown",
    # tempfile
    "tempfile.mkstemp", "tempfile.mkdtemp", "tempfile.NamedTemporaryFile",
    "tempfile.TemporaryFile", "tempfile.TemporaryDirectory",
    # time / random — non-determinism
    "time.time", "time.monotonic", "time.perf_counter", "time.process_time",
    "time.sleep", "time.localtime", "time.gmtime",
    "random.random", "random.randint", "random.choice", "random.shuffle",
    "random.uniform", "random.sample", "random.seed",
    "secrets.token_bytes", "secrets.token_hex", "secrets.token_urlsafe",
    "secrets.choice",
    # datetime non-determinism
    "datetime.datetime.now", "datetime.datetime.today",
    "datetime.datetime.utcnow", "datetime.date.today",
    # network / http
    "socket.socket", "socket.create_connection",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.patch", "requests.request",
    "urllib.request.urlopen", "urllib.request.urlretrieve",
    "http.client.HTTPConnection", "http.client.HTTPSConnection",
    # logging / warnings
    "logging.info", "logging.debug", "logging.warning", "logging.error",
    "logging.critical", "logging.exception", "logging.log",
    "warnings.warn",
    # pathlib
    "pathlib.Path.write_text", "pathlib.Path.write_bytes",
    "pathlib.Path.read_text", "pathlib.Path.read_bytes",
    "pathlib.Path.unlink", "pathlib.Path.mkdir", "pathlib.Path.rmdir",
    "pathlib.Path.touch", "pathlib.Path.rename", "pathlib.Path.replace",
    "pathlib.Path.chmod", "pathlib.Path.symlink_to", "pathlib.Path.hardlink_to",
    "pathlib.Path.open",
})

# Methods that are impure when called on a receiver of a known type.
# When a parameter or local has an annotation we can normalize, we use this
# exact table instead of the generic IO_METHOD_NAMES.
TYPE_IMPURE_METHODS: dict[str, frozenset[str]] = {
    "Path": frozenset({
        "write_text", "write_bytes", "read_text", "read_bytes",
        "unlink", "rmdir", "mkdir", "touch", "chmod", "chown",
        "rename", "replace", "symlink_to", "hardlink_to", "open",
        "iterdir", "glob", "rglob", "stat", "lstat", "exists",
        "is_file", "is_dir", "is_symlink", "is_socket", "resolve",
        "samefile", "readlink",
    }),
    "IO": frozenset({
        "write", "writelines", "read", "readline", "readlines",
        "flush", "close", "truncate", "seek",
    }),
    "TextIO": frozenset({
        "write", "writelines", "read", "readline", "readlines",
        "flush", "close", "truncate", "seek",
    }),
    "BinaryIO": frozenset({
        "write", "writelines", "read", "readline", "readlines",
        "flush", "close", "truncate", "seek",
    }),
    "Logger": frozenset({
        "debug", "info", "warning", "error", "critical", "exception", "log",
    }),
}

# Receiver types whose methods never mutate the receiver: they return new
# values (``str.replace``, ``tuple.index``, ``bytes.split``, ...). When the
# receiver type is known to be one of these, a tracked method is pure
# regardless of receiver kind.
IMMUTABLE_RECEIVER_TYPES: frozenset[str] = frozenset({
    "str", "bytes", "int", "float", "bool", "complex",
    "tuple", "frozenset", "NoneType",
})

# Union of every method name we track — used as the coarse filter at the
# fact-extractor level; per-method policy is applied below.
_ALL_TRACKED_METHOD_NAMES: frozenset[str] = (
    IO_METHOD_NAMES
    | COLLECTION_MUTATION_NAMES
    | frozenset().union(*TYPE_IMPURE_METHODS.values())
)


# --- Public API --------------------------------------------------------------

def is_pure(
    store: IndexStore, symbol_id: str
) -> Observations[Observation] | None:
    """Run the purity analysis on the function identified by ``symbol_id``.

    Returns:
        ``None`` if the symbol can't be analyzed (not found, not a function,
        file index missing).

        Empty :class:`Observations` (falsy) if the function appears pure.

        Non-empty :class:`Observations` (truthy) with impurity observations
        as evidence.

    Always follows project-internal CALL edges so a function that's pure
    in its own body but calls an impure helper is flagged with
    :class:`TransitiveImpureCall` items pointing at the immediate impure
    callees.
    """
    ctx = AnalysisContext.for_function(store, symbol_id)
    if isinstance(ctx, ContextError):
        return None
    direct = Observations(tuple(_iter_observations(ctx)))

    graph = call_graph(store)
    reachable = functions_reachable_from(graph, ctx.function_symbol.symbol_id)
    local_impure: dict[str, bool] = {}
    for sid in reachable:
        if sid == ctx.function_symbol.symbol_id:
            local_impure[sid] = bool(direct)
            continue
        sub_ctx = AnalysisContext.for_function(store, sid)
        if isinstance(sub_ctx, ContextError):
            local_impure[sid] = False
        else:
            local_impure[sid] = any(_iter_observations(sub_ctx))

    impure_set = {sid for sid, is_impure in local_impure.items() if is_impure}
    transitive_callees: dict[str, set[str]] = defaultdict(set)
    changed = True
    while changed:
        changed = False
        for caller in reachable:
            if caller in impure_set:
                continue
            for callee in graph.get(caller, frozenset()):
                if callee in impure_set:
                    impure_set.add(caller)
                    transitive_callees[caller].add(callee)
                    changed = True
                    break

    target = ctx.function_symbol.symbol_id
    if target not in impure_set:
        return direct
    extra = tuple(
        TransitiveImpureCall(callee=c)
        for c in sorted(transitive_callees.get(target, set()))
    )
    return Observations(tuple(direct) + extra)


# --- Internals ---------------------------------------------------------------

# Attribute-write receivers that make a write impure: a parameter (mutating the
# caller's object) or an imported module (global state). Writing to ``self`` /
# ``cls`` or a local variable is pure-local — modifying your own object is fine.
_IMPURE_WRITE_RECEIVERS = frozenset({ReceiverKind.PARAMETER, ReceiverKind.IMPORT})


def _iter_observations(ctx: AnalysisContext) -> Iterable[Observation]:
    """Yield every observation the purity composition collects."""
    yield from outer_scope_writes(ctx)
    yield from _filtered_attribute_writes(ctx)
    yield from bare_calls(ctx, IMPURE_BUILTINS)
    yield from module_calls(ctx, MODULE_IMPURE_NAMES)
    yield from _filtered_attribute_method_calls(ctx)


def _filtered_attribute_writes(ctx: AnalysisContext) -> Iterable[AttributeWrite]:
    """Attribute writes that escape the object: parameter or module receivers.

    ``self`` / ``cls`` and local-variable attribute writes are pure-local —
    consistent with the receiver-kind policy for attribute method calls.
    """
    for write in attribute_writes(ctx):
        if write.receiver_kind in _IMPURE_WRITE_RECEIVERS:
            yield write


def _filtered_attribute_method_calls(
    ctx: AnalysisContext,
) -> Iterable[AttributeMethodCall]:
    """Apply purity-specific policy when interpreting attribute method calls.

    A known immutable receiver type (``str``, ``tuple``, ``bytes``, ...) is
    always pure — its methods return new values rather than mutating.

    Otherwise the type-aware path takes priority: if the receiver root has a
    known type annotation that's in :data:`TYPE_IMPURE_METHODS`, we match the
    leaf against that type's exact method set.

    Failing that we fall back to receiver-kind dispatch:
      * PARAMETER     — flag any tracked method (caller-visible)      * SELF / VARIABLE / UNKNOWN — flag only I/O methods (collection
                                    mutations on locals/self/dynamic
                                    receivers are ignored)
    """
    for call in attribute_method_calls(ctx, _ALL_TRACKED_METHOD_NAMES):
        if call.receiver_type in IMMUTABLE_RECEIVER_TYPES:
            # Methods on immutable types return new values — never a mutation.
            continue
        if call.receiver_type and call.receiver_type in TYPE_IMPURE_METHODS:
            if call.method in TYPE_IMPURE_METHODS[call.receiver_type]:
                yield call
            continue
        if call.receiver_kind == ReceiverKind.PARAMETER:
            yield call
            continue
        if call.method in IO_METHOD_NAMES:
            yield call
