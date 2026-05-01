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

# Methods that are impure on any receiver (I/O is I/O regardless of who owns
# the file handle / socket / path).
IO_METHOD_NAMES: frozenset[str] = frozenset({
    # pathlib
    "write_text", "write_bytes", "read_text", "read_bytes",
    "unlink", "rmdir", "touch", "chmod",
    # file objects
    "write", "writelines", "read", "readline", "readlines",
    "flush", "truncate",
    # socket
    "send", "sendall", "sendto", "recv", "recvfrom",
    "connect", "bind", "listen", "accept", "shutdown",
    # process / system
    "system", "popen", "spawn",
    # filesystem mutation
    "remove", "mkdir", "makedirs", "rename", "replace", "chown", "symlink",
})

# Methods that mutate their receiver but are pure-local — only impure when
# the receiver is a parameter (or unknown).
COLLECTION_MUTATION_NAMES: frozenset[str] = frozenset({
    "append", "extend", "insert", "pop", "clear", "update", "setdefault",
    "sort", "reverse", "add", "discard", "popitem",
})

# Builtin functions that are impure (kept from the original denylist).
IMPURE_BUILTINS: frozenset[str] = frozenset({
    "print", "open", "input", "exec", "eval", "compile",
    "breakpoint", "exit", "quit", "help",
})
