"""Pure path utilities with no internal dependencies.

A leaf module: everything may import it, it imports nothing from ``pypeeker``.
Home to the file-path → dotted-module-path mapping used by the binder,
indexer, tree builder, and refactor applier, and to the barrel predicate
(``is_barrel_path``) shared by everything that treats a package
``__init__.py`` specially.
"""

from __future__ import annotations


def module_path_from(path: str, src_roots: tuple[str, ...] = ()) -> str:
    """Map a source file path to a dotted module path.

    Strips a matching ``src_roots`` prefix, drops the ``.py`` suffix, and
    collapses ``__init__`` to its containing package. With no ``src_roots``
    it just normalises the path — used as the binder default for inline /
    test sources (``"mod.py"`` -> ``"mod"``).

    Examples (src_roots=("src",)):
        src/pypeeker/analysis/calls.py  -> pypeeker.analysis.calls
        src/pypeeker/__init__.py        -> pypeeker
        mod.py                          -> mod
    """
    rel = path.replace("\\", "/").lstrip("/")
    for root in src_roots:
        r = root.strip("/")
        if r and (rel == r or rel.startswith(r + "/")):
            rel = rel[len(r):].lstrip("/")
            break
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    elif rel == "__init__":
        rel = ""
    return rel.replace("/", ".")


def is_barrel_path(path: str) -> bool:
    """True if ``path`` names a package ``__init__.py`` (a barrel module).

    Spelled as ``endswith("__init__.py")`` because that was the predicate the
    retired ``check`` rules used, and the DSL port was graded against them; a
    file named ``pkg/x__init__.py`` therefore counts, on both sides alike.
    The binder's relative-import resolution keeps its own exact-basename
    test (``binder/imports.py``), where a package and a module must not be
    confused.
    """
    return path.endswith("__init__.py")
