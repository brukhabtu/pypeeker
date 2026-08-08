"""The source root is itself a package, so this file's module path is empty.

`paths.module_path_from("src/__init__.py", ("src",))` returns "", the binder
emits no MODULE symbol, and every symbol here has an id like ":impure_root".
The frozen rule matches `include` against the symbol id and against
`symbol_id.split(":", 1)[0]` — "" here — so the `src*` pattern below must NOT
select this function. A port matching against the row's `module` field, which
falls back to the file path when there is no MODULE symbol, flags it.
"""


def impure_root(path):
    """Impure by inspection, and out of scope all the same."""
    return open(path)
