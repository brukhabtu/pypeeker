"""Shared state for one bind() run.

The :class:`BinderState` holds everything the topical visitor functions
read or mutate during a single AST walk. It is constructed at the start
of :func:`pypeeker.binder.binder.bind`, mutated as visitors descend the
tree, and discarded once the resulting :class:`FileIndex` is built.

Because the state has bounded lifetime within one bind() call and never
escapes the binder package, internal mutation by the topical visitor
functions is fine — the composed ``bind()`` function is pure from the
outside (same input → same output, no observable side effects).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pypeeker.adapters import PythonAdapter
from pypeeker.binder.scope_stack import ScopeStack
from pypeeker.models import Reference, Scope, Symbol


@dataclass
class BinderState:
    """Mutable scratch state for one bind() run."""

    adapter: PythonAdapter
    file_path: str
    """Physical path of the source file — used for ``location`` (file:line:col)."""
    module_path: str
    """Dotted semantic module path (e.g. ``pypeeker.analysis.calls``) — the
    root of every ``symbol_id`` produced in this module."""
    source: bytes
    scope_stack: ScopeStack = field(default_factory=ScopeStack)
    symbols: list[Symbol] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    declaration_nodes: set[tuple[int, int]] = field(default_factory=set)
    """Nodes already handled as part of a declaration; ``visit_identifier``
    skips them to avoid double-emitting references."""
