"""Cross-module resolution.

References to imported names bind to the *local* import symbol (the binder is
per-file and can't see other modules). This resolver closes that gap: given an
import, it follows ``imported_from`` — through any number of ``__init__.py``
barrel re-exports — to the canonical definition's symbol id, using the dotted
module paths established by the symbol tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference
from pypeeker.models.symbol_id import (
    is_unresolved_attr,
    module_of,
    unresolved_attr_name,
)
from pypeeker.models.symbols import Symbol, SymbolKind

_TYPED_RECEIVER_KINDS = (SymbolKind.PARAMETER, SymbolKind.VARIABLE)


class _ResolutionKind(str, Enum):
    """How a reference was matched to a canonical definition.

    Lets consumers (LLMs, rename) calibrate trust per match: the first three
    kinds follow explicit name bindings, while the receiver kinds depend on
    type information of the receiver — DECLARED annotations are trustworthy,
    constructor-INFERRED types are best-effort.
    """

    DIRECT = "direct"
    """The reference binds straight to the definition — no import hops."""

    IMPORT_ALIAS = "import_alias"
    """Resolved through one or more imports, none of them an ``__init__.py``
    barrel re-export."""

    BARREL = "barrel"
    """The import chain crosses a re-export in an ``__init__.py`` barrel."""

    RECEIVER_DECLARED = "receiver_declared"
    """An attribute access resolved by walking the receiver chain using only
    DECLARED annotations, ``self`` / ``cls``, or module/class receivers."""

    RECEIVER_INFERRED = "receiver_inferred"
    """The receiver walk needed at least one constructor-*inferred* type —
    the lowest-confidence match."""


@dataclass(frozen=True)
class ResolvedReference:
    """A reference that matched a definition, tagged with how it resolved."""

    reference: Reference
    via: _ResolutionKind


def bare_type_name(annotation: str | None) -> str | None:
    """Normalize a raw type annotation to a single bare type name.

    Handles the common shapes seen in real code: ``Path``, ``pathlib.Path``,
    ``Path | None``, ``Optional[Path]``, ``Union[Path, str]``, ``list[int]``.
    Returns the leftmost concrete name, with module prefix and generic args
    stripped. None for empty / unparseable annotations.

    Intentionally simple — full type resolution is out of scope.
    """
    if not annotation:
        return None
    s = annotation.strip()
    if s.startswith("Optional[") and s.endswith("]"):
        s = s[len("Optional["):-1].strip()
    if s.startswith("Union[") and s.endswith("]"):
        s = s[len("Union["):-1].split(",", 1)[0].strip()
    if "|" in s:
        s = s.split("|", 1)[0].strip()
    if "[" in s:
        s = s[: s.index("[")].strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s or None


class CrossModuleResolver:
    """Resolve import aliases and re-exports to canonical definition ids."""

    def __init__(self, indexes: list[FileIndex]) -> None:
        self._symbols: dict[str, Symbol] = {}
        self._modules: set[str] = set()
        # container scope_id -> {member name -> symbol}. Because a class's
        # scope_id equals its symbol_id and module-level symbols have
        # parent_scope_id == the module path, this one map covers both module
        # members and class members.
        self._members: dict[str, dict[str, Symbol]] = {}
        self._references: list[Reference] = []
        self._cache: dict[str, str] = {}

        for index in indexes:
            self._references.extend(index.references)
            for symbol in index.symbols:
                self._symbols[symbol.symbol_id] = symbol
                if symbol.kind == SymbolKind.MODULE:
                    self._modules.add(symbol.symbol_id)

        for symbol in self._symbols.values():
            if symbol.parent_scope_id is not None:
                self._members.setdefault(symbol.parent_scope_id, {})[
                    symbol.name
                ] = symbol

    # Cap on receiver-chain length we will walk (``a.b.c`` is 3). Bounds the
    # field-dereference work and avoids chasing long, low-confidence chains.
    _MAX_RECEIVER_HOPS = 3

    def resolve_reference(self, ref: Reference, *, declared_only: bool = False) -> str:
        """Canonical definition a reference binds to, resolving qualified access.

        The binder leaves an ``<unresolved>.attr`` id for attribute access but
        records the receiver root + chain. We walk the chain (up to
        :data:`_MAX_RECEIVER_HOPS`): each step looks up a member in the current
        container and follows that member's type to the next container, so
        ``receiver.field.method()`` resolves through the field's type. All other
        references fall back to :meth:`resolve_definition`.

        With ``declared_only``, receiver steps that rely on a constructor-
        *inferred* type are not followed (only DECLARED annotations, ``self`` /
        ``cls``, and module/class receivers) — used by rename, which mutates
        code and must not act on best-effort inference.
        """
        sid = ref.symbol_id
        if (
            is_unresolved_attr(sid)
            and ref.receiver_root_symbol_id is not None
            and ref.receiver_chain
        ):
            attr = unresolved_attr_name(sid)
            target = self._resolve_attr(
                ref.receiver_root_symbol_id,
                ref.receiver_chain,
                attr,
                declared_only=declared_only,
            )
            if target is not None:
                return target
        return self.resolve_definition(sid)

    def _resolve_attr(
        self,
        receiver_root_id: str,
        receiver_chain: list[str],
        attr: str,
        *,
        declared_only: bool = False,
    ) -> str | None:
        """Resolve ``root.<chain...>.attr`` to a member's canonical id, or None.

        Walks the receiver chain: starting from the root's container (a module,
        class, or the class behind a typed/self receiver), each intermediate
        name is resolved to a member and that member's type gives the next
        container; finally ``attr`` is looked up in the last container. Capped
        at :data:`_MAX_RECEIVER_HOPS`. Best-effort and query-only.
        """
        if len(receiver_chain) > self._MAX_RECEIVER_HOPS:
            return None
        container = self._container_of(receiver_root_id, declared_only=declared_only)
        if container is None:
            return None
        for name in receiver_chain[1:]:
            field = self._members.get(container, {}).get(name)
            if field is None:
                return None
            container = self._container_of(field.symbol_id, declared_only=declared_only)
            if container is None:
                return None
        member = self._members.get(container, {}).get(attr)
        if member is not None:
            return self.resolve_definition(member.symbol_id)
        return None

    _MAX_TYPE_HOPS = 3

    def _container_of(self, symbol_id: str, *, declared_only: bool = False) -> str | None:
        """The id whose members an attribute of ``symbol_id`` lives under.

        Resolves, in order: a module or class to itself; a callable (function /
        method / property) to the class of its return type; a parameter or
        variable to the class of its declared/inferred type; and ``self`` /
        ``cls`` to the enclosing class.
        """
        resolved = self.resolve_definition(symbol_id)
        if resolved in self._modules:
            return resolved
        target = self._symbols.get(resolved)
        if target is not None:
            if target.kind == SymbolKind.CLASS:
                return resolved
            # A callable receiver (incl. @property) — its container is the
            # class of its return type. Return annotations are declared, so this
            # path is unaffected by ``declared_only``.
            if (
                target.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
                and target.type_annotation is not None
            ):
                return self._class_from_type_name(
                    target.type_annotation.raw, resolved
                )

        origin = self._symbols.get(symbol_id)
        if origin is None or origin.kind not in _TYPED_RECEIVER_KINDS:
            return None
        if origin.type_annotation is not None and not (
            declared_only
            and origin.type_annotation.confidence is not Confidence.DECLARED
        ):
            container = self._class_from_type_name(
                origin.type_annotation.raw, origin.symbol_id
            )
            if container is not None:
                return container
        if origin.kind == SymbolKind.PARAMETER and origin.name in ("self", "cls"):
            method = self._symbols.get(origin.parent_scope_id)
            if method is not None and method.kind == SymbolKind.METHOD:
                return method.parent_scope_id  # the enclosing class
        return None

    def _class_from_type_name(
        self, raw: str, owner_id: str, _depth: int = 0
    ) -> str | None:
        """Resolve a type-annotation string to a class id, in ``owner_id``'s module.

        If the name resolves to a function/method (e.g. a factory or a property
        used as an intermediate receiver), follow its return type. Bounded by
        :data:`_MAX_TYPE_HOPS` to terminate on self-referential return types.
        """
        if _depth > self._MAX_TYPE_HOPS:
            return None
        type_name = bare_type_name(raw)
        if type_name is None:
            return None
        module = module_of(owner_id)
        type_sym = self._members.get(module, {}).get(type_name)
        if type_sym is None:
            return None
        resolved = self.resolve_definition(type_sym.symbol_id)
        rsym = self._symbols.get(resolved)
        if rsym is None:
            return None
        if rsym.kind == SymbolKind.CLASS:
            return resolved
        if (
            rsym.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
            and rsym.type_annotation is not None
        ):
            return self._class_from_type_name(
                rsym.type_annotation.raw, resolved, _depth + 1
            )
        return None

    def resolve_definition(self, symbol_id: str) -> str:
        """Return the canonical definition id for ``symbol_id``.

        Follows IMPORT -> definition transitively (including barrel
        re-exports). Idempotent for definitions and for external/stdlib
        imports, which resolve to themselves.
        """
        if symbol_id in self._cache:
            return self._cache[symbol_id]
        result = self._resolve_chain(symbol_id)[-1]
        self._cache[symbol_id] = result
        return result

    def crosses_barrel(self, symbol_id: str) -> bool:
        """True if resolving ``symbol_id`` traverses a re-export in an __init__.

        A barrel consumer (``from pkg import X`` where ``pkg/__init__.py``
        re-exports ``X`` from a submodule) resolves *through* the package's
        ``__init__`` import; a direct import (``from pkg.sub import X``) does
        not. This lets rename gate barrel-routed import updates behind
        ``--include-exports``, since rewriting such an import is only valid
        once the re-export it depends on is also updated.
        """
        chain = self._resolve_chain(symbol_id)
        # The last id is the definition; every prior id is an import hop.
        for hop_id in chain[:-1]:
            symbol = self._symbols.get(hop_id)
            if (
                symbol is not None
                and symbol.kind == SymbolKind.IMPORT
                and symbol.location.file_path.endswith("__init__.py")
            ):
                return True
        return False

    def _resolve_chain(self, symbol_id: str) -> list[str]:
        """Walk IMPORT -> definition, returning every id visited in order.

        The final element is the canonical definition (or the last reachable
        node for external/circular cases); earlier elements are the import
        hops traversed to get there.
        """
        chain: list[str] = []
        seen: set[str] = set()
        current = symbol_id

        while True:
            chain.append(current)
            if current in seen:
                break  # circular re-export — stop at the last node seen
            seen.add(current)

            symbol = self._symbols.get(current)
            if symbol is None or symbol.kind != SymbolKind.IMPORT:
                break
            imported_from = symbol.imported_from
            if not imported_from:
                break

            # ``import pkg.mod`` / ``import pkg.mod as m`` — the import names a
            # module directly.
            if imported_from in self._modules:
                chain.append(imported_from)
                break

            module_part, _, name = imported_from.rpartition(".")
            if not module_part or module_part not in self._modules:
                break  # external / stdlib / unindexed module

            target = self._members.get(module_part, {}).get(name)
            if target is None:
                break  # name not found in the defining module
            current = target.symbol_id

        return chain

    def references_to_definition(
        self, symbol_id: str, *, declared_only: bool = False
    ) -> list[Reference]:
        """Every reference across the project that resolves to a definition.

        Matches on the *canonical definition*, not the local binding: each
        reference is resolved (through import aliases, barrel re-exports, and
        qualified attribute/method access) and kept if its canonical
        definition matches that of ``symbol_id``. With ``declared_only``,
        matches classified :attr:`ResolutionKind.RECEIVER_INFERRED` are
        excluded — receiver resolution that relies on constructor-inferred
        types (see :meth:`resolve_reference`).

        A thin filter over :meth:`references_to_definition_classified`, so the
        classification is the single code path deciding what "declared only"
        means.
        """
        classified = self.references_to_definition_classified(symbol_id)
        if declared_only:
            classified = [
                c
                for c in classified
                if c.via is not _ResolutionKind.RECEIVER_INFERRED
            ]
        return [c.reference for c in classified]

    def references_to_definition_classified(
        self, symbol_id: str
    ) -> list[ResolvedReference]:
        """Like :meth:`references_to_definition`, with each match tagged by
        *how* it resolved (a :class:`ResolutionKind`).

        Receiver matches are classified by re-resolving with
        ``declared_only=True``: if the declared-only walk reaches the same
        canonical definition the match is :attr:`ResolutionKind.RECEIVER_DECLARED`,
        otherwise the walk leaned on a constructor-inferred type and the match
        is :attr:`ResolutionKind.RECEIVER_INFERRED`. Simple and correct at the
        cost of one extra (bounded) walk per receiver match.
        """
        canonical = self.resolve_definition(symbol_id)
        return [
            ResolvedReference(ref, self._classify(ref, canonical))
            for ref in self._references
            if self.resolve_reference(ref) == canonical
        ]

    def _classify(self, ref: Reference, canonical: str) -> _ResolutionKind:
        """The :class:`ResolutionKind` of a reference known to match ``canonical``."""
        sid = ref.symbol_id
        if (
            is_unresolved_attr(sid)
            and ref.receiver_root_symbol_id is not None
            and ref.receiver_chain
        ):
            # Matched via the receiver walk (the ``<unresolved>.attr`` sentinel
            # can never itself equal a canonical definition id).
            if self.resolve_reference(ref, declared_only=True) == canonical:
                return _ResolutionKind.RECEIVER_DECLARED
            return _ResolutionKind.RECEIVER_INFERRED
        chain = self._resolve_chain(sid)
        if len(chain) == 1:
            return _ResolutionKind.DIRECT
        if self.crosses_barrel(sid):
            return _ResolutionKind.BARREL
        return _ResolutionKind.IMPORT_ALIAS
