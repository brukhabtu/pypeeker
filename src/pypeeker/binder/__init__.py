"""Binder: walks tree-sitter CSTs into structured semantic models.

This package is the Python-specific binding half of the Python language
adapter (the full adapter is {``adapters.python_adapter`` + ``binder`` +
``refactor.cst``}; see ``pypeeker.adapters``). It deliberately hardcodes
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
