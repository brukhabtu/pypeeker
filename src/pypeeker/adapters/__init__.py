"""Language adapters: per-language parsing and conventions.

The language-agnostic contract of this codebase is
:class:`~pypeeker.models.index.FileIndex`, not a class in this package.
The Python "adapter" is really a package boundary spanning three modules:

- :mod:`pypeeker.adapters.python_adapter` — tree-sitter parsing and
  visibility conventions (lives here)
- :mod:`pypeeker.binder` — the Python-specific binding half: walks the
  Python CST into ``FileIndex``
- :mod:`pypeeker.refactor.cst` — Python-CST edit helpers for refactors

Supporting a second language means providing equivalents of all three that
emit the same ``FileIndex`` shape. :class:`~pypeeker.adapters.base.LanguageAdapter`
documents only the slice of that boundary consumers call directly.
"""

from pypeeker.adapters.python_adapter import PythonAdapter

__all__ = ["PythonAdapter"]
