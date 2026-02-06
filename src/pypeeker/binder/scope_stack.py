"""Scope stack for name resolution during binding."""

from __future__ import annotations

from dataclasses import dataclass, field

from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol


@dataclass
class ScopeEntry:
    """Internal entry in the scope stack."""

    scope: Scope
    declarations: dict[str, list[Symbol]] = field(default_factory=dict)
    globals_declared: set[str] = field(default_factory=set)
    nonlocals_declared: set[str] = field(default_factory=set)

    def declaration_count(self, name: str) -> int:
        return len(self.declarations.get(name, []))

    def add_declaration(self, name: str, symbol: Symbol) -> None:
        self.declarations.setdefault(name, []).append(symbol)

    def lookup_local(self, name: str) -> Symbol | None:
        decls = self.declarations.get(name)
        if decls:
            return decls[-1]  # Most recent declaration
        return None


class ScopeStack:
    """Maintains the current scope chain during AST walking."""

    def __init__(self) -> None:
        self._stack: list[ScopeEntry] = []

    def push(self, scope: Scope) -> None:
        self._stack.append(ScopeEntry(scope=scope))

    def pop(self) -> Scope:
        return self._stack.pop().scope

    @property
    def current(self) -> ScopeEntry:
        return self._stack[-1]

    @property
    def current_scope(self) -> Scope:
        return self._stack[-1].scope

    @property
    def depth(self) -> int:
        return len(self._stack)

    @property
    def module_entry(self) -> ScopeEntry:
        return self._stack[0]

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

    def declare_in_scope(self, name: str, symbol: Symbol, target_entry: ScopeEntry) -> str:
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
        nested function scopes via normal name lookup.
        """
        # L: Local scope
        local = self.current.lookup_local(name)
        if local:
            return local

        # E + G: Walk up enclosing scopes, skip class scopes
        for i in range(len(self._stack) - 2, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.CLASS:
                continue
            found = entry.lookup_local(name)
            if found:
                return found

        return None

    def find_global_target(self) -> ScopeEntry:
        """Find the module-level scope entry for `global` declarations."""
        return self._stack[0]

    def find_nonlocal_target(self, name: str) -> ScopeEntry | None:
        """Find the nearest enclosing function scope for `nonlocal` declarations."""
        for i in range(len(self._stack) - 2, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.FUNCTION:
                return entry
        return None

    def find_enclosing_function_entry(self) -> ScopeEntry | None:
        """Find the nearest enclosing function scope entry (for walrus in comprehensions)."""
        for i in range(len(self._stack) - 1, -1, -1):
            entry = self._stack[i]
            if entry.scope.kind == ScopeKind.FUNCTION:
                return entry
        # Fall back to module scope
        return self._stack[0] if self._stack else None

    def build_scope_chain(self, file_path: str) -> str:
        """Build the dot-separated scope chain for symbol IDs.

        Module scope is represented by just the file_path.
        Named scopes (class, function) use dots.
        """
        parts: list[str] = []
        for entry in self._stack:
            if entry.scope.kind == ScopeKind.MODULE:
                continue
            parts.append(entry.scope.name)
        if parts:
            return f"{file_path}:{'.'.join(parts)}"
        return file_path

    def build_symbol_id(self, file_path: str, name: str, is_scope_creator: bool = False) -> str:
        """Build a symbol ID from the current scope chain.

        For scope-creating symbols (functions, classes), they use dot notation
        as they form part of the scope chain. For local names (variables,
        parameters), they use colon notation.

        Format: file:ScopeChain.With.Dots:local_with_colons
        """
        scope_chain = self.build_scope_chain(file_path)
        if is_scope_creator:
            # This symbol creates a scope — it's part of the dot chain
            if scope_chain == file_path:
                return f"{file_path}:{name}"
            return f"{scope_chain}.{name}"
        else:
            # This is a local/param — use colon separator
            if scope_chain == file_path:
                return f"{file_path}:{name}"
            return f"{scope_chain}:{name}"
