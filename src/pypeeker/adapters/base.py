"""Language adapter boundary.

There is deliberately no ``LanguageAdapter`` class or protocol: the surface
consumers actually call today is small — parsing source to a tree-sitter CST,
naming the language, and classifying name visibility — and it lives on the
concrete adapter. The real language-agnostic seam in this codebase is
:class:`~pypeeker.models.index.FileIndex`: everything downstream of the binder
(storage, query, analysis, check, refactor planning) consumes ``FileIndex``
and never touches language-specific code.

In practice the "Python adapter" is a package boundary, not a single class:

- ``pypeeker.adapters.python_adapter`` — parsing + visibility conventions
- ``pypeeker.binder`` — walks the Python CST into ``FileIndex``
  (hardcodes tree-sitter-python node types by design)
- ``pypeeker.refactor.cst`` — Python-CST edit helpers for refactors

A second language would supply equivalents of all three, producing the same
``FileIndex`` shape.
"""
