"""Scope stack for name resolution during binding."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from pypeeker.models import Scope, ScopeKind, Symbol, SymbolKind


@dataclass
class _ScopeEntry:
    """Internal entry in the scope stack."""

    scope: Scope
    declarations: dict[str, list[Symbol]] = field(default_factory=dict)
    globals_declared: set[str] = field(default_factory=set)
    nonlocals_declared: set[str] = field(default_factory=set)

    def declaration_count(self, name: str) -> int:
        """How many times ``name`` has been declared in this scope (shadowing counter)."""
        return len(self.declarations.get(name, []))

    def add_declaration(self, name: str, symbol: Symbol) -> None:
        """Record a declaration of ``name`` in this scope."""
        self.declarations.setdefault(name, []).append(symbol)

    def lookup_local(self, name: str) -> Symbol | None:
        """Most recent declaration of ``name`` in this scope, or ``None``."""
        decls = self.declarations.get(name)
        if decls:
            return decls[-1]  # Most recent declaration
        return None


class ScopeStack:
    """Maintains the current scope chain during AST walking."""

    def __init__(self) -> None:
        self._stack: list[_ScopeEntry] = []
        self._type_param_overlays: list[dict[str, Symbol]] = []

    def push(self, scope: Scope) -> None:
        """Enter a new scope."""
        self._stack.append(_ScopeEntry(scope=scope))

    def pop(self) -> Scope:
        """Leave the current scope and return it."""
        return self._stack.pop().scope

    def pop_entry(self) -> _ScopeEntry:
        """Leave the current scope, returning its *entry* — declarations and
        all — so :meth:`push_entry` can re-enter the very same scope later.

        Used by the binder for a PEP 695 generic definition: its type
        parameters must be declared inside the definition's own scope (that is
        where their symbol ids and every later lookup live), but the header
        they scope over — base-class list, return annotation, alias value — is
        evaluated in the *enclosing* scope. So the binder pushes the scope,
        declares the type parameters, steps back out with ``pop_entry``, binds
        the header under :meth:`type_param_overlay`, and steps back in.
        """
        return self._stack.pop()

    def push_entry(self, entry: _ScopeEntry) -> None:
        """Re-enter a scope entry previously removed by :meth:`pop_entry`."""
        self._stack.append(entry)

    @contextmanager
    def type_param_overlay(self, type_params: dict[str, Symbol]) -> Iterator[None]:
        """Make ``type_params`` visible to :meth:`resolve` without entering a scope.

        This models PEP 695's implicit annotation scope, which sits *between* a
        generic definition and its parent: names in it are visible from the
        definition's header, yet the header still sees the immediately
        enclosing class namespace (which a real nested scope would hide, since
        the outward walk skips class scopes). CPython agrees — ``class C:
        Alias = int; def m[T](self) -> Alias: ...`` resolves ``Alias``.
        """
        self._type_param_overlays.append(type_params)
        try:
            yield
        finally:
            self._type_param_overlays.pop()

    @property
    def current(self) -> _ScopeEntry:
        """The innermost scope entry (with its declarations and globals/nonlocals)."""
        return self._stack[-1]

    @property
    def current_scope(self) -> Scope:
        """The innermost ``Scope`` object."""
        return self._stack[-1].scope

    def declare(self, name: str, symbol: Symbol) -> str:
        """Declare a name in the current scope.

        Handles shadowing: first occurrence gets no suffix,
        second gets $2, third gets $3, etc.

        Returns the final symbol_id (potentially with $N suffix).
        """
        entry = self.current
        count = entry.declaration_count(name)
        if count > 0:
            suffix = f"${count + 1}"
            symbol.symbol_id = symbol.symbol_id + suffix
        entry.add_declaration(name, symbol)
        return symbol.symbol_id

    def declare_in_scope(self, name: str, symbol: Symbol, target_entry: _ScopeEntry) -> str:
        """Declare a name in a specific scope (for global/nonlocal)."""
        count = target_entry.declaration_count(name)
        if count > 0:
            suffix = f"${count + 1}"
            symbol.symbol_id = symbol.symbol_id + suffix
        target_entry.add_declaration(name, symbol)
        return symbol.symbol_id

    def resolve(self, name: str) -> Symbol | None:
        """Resolve a name by walking up the scope chain (LEGB).

        Skips class scopes — Python class scope is not accessible from
        nested function scopes via normal name lookup — with one exception:
        a PEP 695 type parameter declared on the class (``class C[T]``) lives
        in an implicit scope *around* the class, so it stays visible from
        nested method bodies. A class scope hit is honored only when it
        resolves to a TYPE_PARAMETER symbol; any other kind on a class scope
        is skipped exactly as before.

        An active :meth:`type_param_overlay` is consulted first: it stands for
        the PEP 695 annotation scope, which is innermost while a generic
        definition's header is being bound, so a type parameter shadows a
        same-named enclosing binding there.

        The class-scope exception yields to an explicit ``global``/``nonlocal``
        in the *current* scope: ``global T`` inside a method of ``class C[T]``
        rebinds the name to the module global, and CPython reads the global
        there. Only the current scope's declarations count — a function nested
        inside that method has no ``global`` of its own and does see the type
        parameter.
        """
        for overlay in reversed(self._type_param_overlays):
            found = overlay.get(name)
            if found is not None:
                return found

        # L: Local scope
        local = self.current.lookup_local(name)
        if local:
            return local

        rebound = (
            name in self.current.globals_declared or name in self.current.nonlocals_declared
        )

        # E + G: Walk up enclosing scopes, skip class scopes
        for i in range(len(self._stack) - 2, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.CLASS:
                found = entry.lookup_local(name)
                if not rebound and found is not None and found.kind is SymbolKind.TYPE_PARAMETER:
                    return found
                continue
            found = entry.lookup_local(name)
            if found:
                return found

        return None

    def find_global_target(self) -> _ScopeEntry:
        """Find the module-level scope entry for `global` declarations."""
        return self._stack[0]

    def find_nonlocal_target(self, name: str) -> _ScopeEntry | None:
        """Find the nearest enclosing function scope for `nonlocal` declarations."""
        for i in range(len(self._stack) - 2, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.FUNCTION:
                return entry
        return None

    def find_enclosing_function_entry(self) -> _ScopeEntry | None:
        """Find the nearest enclosing function scope entry (for walrus in comprehensions)."""
        for i in range(len(self._stack) - 1, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.FUNCTION:
                return entry
        # Fall back to module scope
        return self._stack[0] if self._stack else None

    def find_enclosing_class(self) -> Scope | None:
        """Find the nearest enclosing class scope, if any."""
        for i in range(len(self._stack) - 1, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.CLASS:
                return entry.scope
        return None

    def get_class_scope_entry(self, class_scope_id: str) -> _ScopeEntry | None:
        """Get the ScopeEntry for a specific class scope ID."""
        for entry in self._stack:
            if entry.scope.scope_id == class_scope_id:
                return entry
        return None

    def build_scope_chain(self, id_root: str) -> str:
        """Build the dot-separated scope chain for symbol IDs.

        ``id_root`` is the dotted module path (e.g. ``pypeeker.analysis.calls``).
        Module scope is represented by just the root; named scopes (class,
        function) append with dots after a ``:`` separator.
        """
        parts: list[str] = []
        for entry in self._stack:
            if entry.scope.kind == ScopeKind.MODULE:
                continue
            parts.append(entry.scope.name)
        if parts:
            return f"{id_root}:{'.'.join(parts)}"
        return id_root

    def build_symbol_id(self, id_root: str, name: str, is_scope_creator: bool = False) -> str:
        """Build a symbol ID from the current scope chain.

        For scope-creating symbols (functions, classes), they use dot notation
        as they form part of the scope chain. For local names (variables,
        parameters), they use colon notation.

        Format: module.path:ScopeChain.With.Dots:local_with_colons
        """
        scope_chain = self.build_scope_chain(id_root)
        if is_scope_creator:
            # This symbol creates a scope — it's part of the dot chain
            if scope_chain == id_root:
                return f"{id_root}:{name}"
            return f"{scope_chain}.{name}"
        else:
            # This is a local/param — use colon separator
            if scope_chain == id_root:
                return f"{id_root}:{name}"
            return f"{scope_chain}:{name}"
