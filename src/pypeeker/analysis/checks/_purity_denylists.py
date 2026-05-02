"""Denylists used by the purity check.

Three categories that interact differently with the receiver root:

* MODULE_IMPURE_NAMES — fully-qualified external names (``os.system``,
  ``pathlib.Path.write_text``). Matched when the receiver root resolves
  to an IMPORT symbol and we can compute the full name from
  ``imported_from + chain + leaf``.

* IO_METHOD_NAMES — attribute method names that are impure regardless
  of receiver type (file I/O, network I/O, system mutation). Flagged
  whenever they appear, even on local variables: ``index_path.write_text``
  is impure regardless of whether ``index_path`` is local.

* COLLECTION_MUTATION_NAMES — attribute method names that mutate the
  receiver but are pure-local for variables you own (``[].append(1)``).
  Flagged only when receiver is a parameter or an unknown/dynamic
  receiver — never when receiver is a local VARIABLE.

These are *purity-specific policy*, not intrinsic semantic facts. A
future DeterminismCheck would subset MODULE_IMPURE_NAMES to just the
non-deterministic entries; a SideEffectCheck might use just IO names.
"""

# Fully-qualified external calls that are impure.
# Built up from imported_from + chain segments + leaf method.
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
    "os.path.exists", "os.path.isfile", "os.path.isdir",  # filesystem reads

    # subprocess
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen", "subprocess.getoutput",
    "subprocess.getstatusoutput",

    # shutil
    "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree",
    "shutil.move", "shutil.rmtree", "shutil.chown",

    # tempfile (creates files)
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

    # pathlib (most Path methods are I/O)
    "pathlib.Path.write_text", "pathlib.Path.write_bytes",
    "pathlib.Path.read_text", "pathlib.Path.read_bytes",
    "pathlib.Path.unlink", "pathlib.Path.mkdir", "pathlib.Path.rmdir",
    "pathlib.Path.touch", "pathlib.Path.rename", "pathlib.Path.replace",
    "pathlib.Path.chmod", "pathlib.Path.symlink_to", "pathlib.Path.hardlink_to",
    "pathlib.Path.open",
})

# Methods that are impure on any receiver (file / network / system I/O is
# I/O regardless of who owns the handle / path / socket).
#
# Names omitted intentionally because they over-match outside their stdlib
# context — moved to MODULE_IMPURE_NAMES (which only fires when the receiver
# root resolves to an import) or left to a future type-aware pass:
#   bind, accept, listen, connect, shutdown — overloaded (binder.bind,
#       visitor.accept, click.listen, signal.connect, executor.shutdown, ...)
#   replace — overloaded (str.replace is the dominant Python idiom; pathlib
#       Path.replace is caught by MODULE_IMPURE_NAMES instead)
#   remove — moved to COLLECTION_MUTATION_NAMES (list.remove / set.remove are
#       pure-local; os.remove / Path.unlink are caught by MODULE_IMPURE_NAMES)
IO_METHOD_NAMES: frozenset[str] = frozenset({
    # pathlib
    "write_text", "write_bytes", "read_text", "read_bytes",
    "unlink", "rmdir", "touch", "chmod",
    # file objects
    "write", "writelines", "read", "readline", "readlines",
    "flush", "truncate",
    # socket — kept the receive-side only (less frequently overloaded than send/bind/etc.)
    "recv", "recvfrom",
    # process / system
    "system", "popen", "spawn",
    # filesystem mutation
    "mkdir", "makedirs", "rename", "chown", "symlink",
})

# Methods that mutate their receiver but are pure-local — only impure when
# the receiver is a parameter (or unknown receiver, conservatively).
COLLECTION_MUTATION_NAMES: frozenset[str] = frozenset({
    "append", "extend", "insert", "pop", "clear", "update", "setdefault",
    "sort", "reverse", "add", "discard", "popitem", "remove",
})

# Methods that are impure when called on a receiver of a known type.
# Used by TASK-14 type-aware dispatch: when a parameter or local has an
# annotation we can normalize (e.g. 'Path', 'IO'), we use this exact
# table instead of the generic IO_METHOD_NAMES.
TYPE_IMPURE_METHODS: dict[str, frozenset[str]] = {
    # pathlib.Path — most methods are I/O. ``parent``, ``name``, ``suffix``,
    # ``with_suffix`` etc. are pure (path string manipulation).
    "Path": frozenset({
        "write_text", "write_bytes", "read_text", "read_bytes",
        "unlink", "rmdir", "mkdir", "touch", "chmod", "chown",
        "rename", "replace", "symlink_to", "hardlink_to", "open",
        "iterdir", "glob", "rglob", "stat", "lstat", "exists",
        "is_file", "is_dir", "is_symlink", "is_socket", "resolve",
        "samefile", "readlink",
    }),
    # File-like protocols (IO, TextIO, BinaryIO from typing).
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
    # logging.Logger — every emit is a side effect.
    "Logger": frozenset({
        "debug", "info", "warning", "error", "critical", "exception",
        "log",
    }),
}

# Union of all method names mentioned in any denylist — used by the fact
# extractor as a single coarse filter. The check applies the precise
# (type-specific or structural) policy.
ALL_TRACKED_METHOD_NAMES: frozenset[str] = (
    IO_METHOD_NAMES
    | COLLECTION_MUTATION_NAMES
    | frozenset().union(*TYPE_IMPURE_METHODS.values())
)


# Builtin functions that are impure (kept from the original denylist).
IMPURE_BUILTINS: frozenset[str] = frozenset({
    "print", "open", "input", "exec", "eval", "compile",
    "breakpoint", "exit", "quit", "help",
})
