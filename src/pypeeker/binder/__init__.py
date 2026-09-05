"""Binder: walks tree-sitter CSTs into structured semantic models.

This package is the binding third of the Python language adapter
(``adapters.python_adapter`` for parsing, ``binder`` for binding,
``refactor.cst`` for CST editing; see ``pypeeker.adapters``). Code generation
and precondition-time CST analysis in ``refactor/`` are not yet behind that
boundary; ``architecture.md`` -> "Language adapter" lists the remaining
Python-specific sites. It deliberately hardcodes
tree-sitter-python node types: its output, the language-agnostic
``FileIndex``, is the seam everything downstream consumes. A second
language would get its own binder producing the same ``FileIndex`` shape.

Import convention inside the package: :mod:`pypeeker.binder.binder` owns the
dispatch table :func:`visit_node` and imports every visitor module, so the
visitor modules (``scopes``, ``assignments``, ``references``, ``imports``)
must not import ``binder`` at module level — that would be a cycle. Any
visitor that needs to recurse does ``from pypeeker.binder.binder import
visit_node`` *inside the function body*; by call time ``binder`` is fully
imported. Sites carry only a short pointer comment back to this paragraph.
"""

from pypeeker.binder.binder import bind, visit_module, visit_node
from pypeeker.binder.state import BinderState

__all__ = ["BinderState", "bind", "visit_module", "visit_node"]
