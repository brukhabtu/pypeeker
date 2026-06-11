"""The symbol-id grammar, owned in one place.

A symbol id has the shape::

    module.path:Scope.Chain:local$N

* ``module.path`` — the dotted module path (everything before the first
  ``:``).
* ``Scope.Chain`` — dot-separated scope-creating names (classes, functions)
  hanging off the module after a ``:`` separator.
* ``local`` — a non-scope-creating name (variable, parameter) attached with a
  second ``:``.
* ``$N`` — an optional shadow suffix: the second declaration of a name in the
  same scope gets ``$2``, the third ``$3``, and so on.

Two sentinel prefixes mark ids that do not point into the project:

* :data:`BUILTINS_PREFIX` (``<builtins>.``) — the reference resolved to a
  Python builtin (``<builtins>.len``); these are *resolved*.
* :data:`UNRESOLVED_PREFIX` (``<unresolved>.``) — an attribute access whose
  receiver could not be resolved (``<unresolved>.method``); the receiver
  root/chain metadata on the reference carries what is known.

This module is the single owner of that grammar: construction of the sentinel
ids and all parsing (module part, leaf name, shadow suffix) live here, so
consumers never re-derive the cases with ad-hoc string surgery.

models is a leaf package — this module must not import anything from
``pypeeker`` outside ``pypeeker.models``.
"""

from __future__ import annotations

BUILTINS_PREFIX = "<builtins>."
"""Prefix of synthetic ids for references resolved to Python builtins."""

UNRESOLVED_PREFIX = "<unresolved>."
"""Prefix of synthetic ids for attribute access on an unresolved receiver."""

_SHADOW_SEP = "$"
"""Separator introducing the shadow ordinal (``x$2`` is the second ``x``)."""


def builtin_id(name: str) -> str:
    """Synthetic symbol id for a reference resolved to a Python builtin."""
    return f"{BUILTINS_PREFIX}{name}"


def is_builtin(symbol_id: str) -> bool:
    """True if ``symbol_id`` is a synthetic builtin id (``<builtins>.X``)."""
    return symbol_id.startswith(BUILTINS_PREFIX)


def builtin_name(symbol_id: str) -> str:
    """The builtin's bare name (``<builtins>.len`` -> ``len``).

    Only meaningful when :func:`is_builtin` is true.
    """
    return symbol_id[len(BUILTINS_PREFIX):]


def unresolved_attr_id(name: str) -> str:
    """Synthetic symbol id for attribute access on an unresolved receiver."""
    return f"{UNRESOLVED_PREFIX}{name}"


def is_unresolved_attr(symbol_id: str) -> bool:
    """True if ``symbol_id`` is an unresolved-attribute id (``<unresolved>.X``)."""
    return symbol_id.startswith(UNRESOLVED_PREFIX)


def unresolved_attr_name(symbol_id: str) -> str:
    """The attribute's bare name (``<unresolved>.method`` -> ``method``).

    Only meaningful when :func:`is_unresolved_attr` is true.
    """
    return symbol_id[len(UNRESOLVED_PREFIX):]


def module_of(symbol_id: str) -> str:
    """The dotted module path of a symbol id — everything before the first ``:``.

    Idempotent for bare module paths (``pkg.mod`` -> ``pkg.mod``).
    """
    return symbol_id.split(":", 1)[0]


def leaf_name(symbol_id: str) -> str:
    """The trailing local/member name of a (possibly unresolved) symbol id.

    Sentinel unresolved-attribute ids yield the attribute name directly.
    Otherwise the last ``.`` segment is taken, then the last ``:`` segment of
    that, covering both grammar shapes: ``mod:Class.method`` -> ``method``
    and ``pkg.mod:f:x`` -> ``x``. A shadow suffix is *not* stripped
    (``m:f:x$2`` -> ``x$2``) — use :func:`strip_shadow` for that.
    """
    if symbol_id.startswith(UNRESOLVED_PREFIX):
        return symbol_id[len(UNRESOLVED_PREFIX):]
    for sep in (".", ":"):
        if sep in symbol_id:
            symbol_id = symbol_id.rsplit(sep, 1)[-1]
    return symbol_id


def strip_shadow(symbol_id: str) -> str:
    """Remove a trailing ``$N`` shadow suffix, if present.

    Ids without a shadow suffix are returned unchanged; a ``$`` not followed
    by digits is not a shadow suffix and is left alone.
    """
    base, sep, ordinal = symbol_id.rpartition(_SHADOW_SEP)
    if sep and ordinal.isdigit():
        return base
    return symbol_id


def shadow_suffix(symbol_id: str) -> int | None:
    """The shadow ordinal ``N`` of a ``$N``-suffixed id, or None.

    The first declaration of a name carries no suffix, so ``None`` means
    "first (or only) binding"; the second declaration yields ``2``, etc.
    """
    base, sep, ordinal = symbol_id.rpartition(_SHADOW_SEP)
    if sep and ordinal.isdigit():
        return int(ordinal)
    return None
