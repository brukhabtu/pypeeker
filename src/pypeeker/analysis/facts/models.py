"""Typed semantic facts produced by the fact extractors.

Facts are pure observations about a function's body — they say what the code
*does*, not whether that's good or bad. Composite checks (e.g. purity,
determinism, side-effects) consume facts and apply policy.

All fact types are frozen dataclasses to enable use in sets / dict keys and
to make accidental mutation impossible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OuterScopeWrite:
    """The function writes to a name resolved outside its scope subtree.

    Captures global / nonlocal mutations after the binder's redirect.
    """

    target_symbol_id: str
    line: int


@dataclass(frozen=True)
class AttributeWrite:
    """The function writes to an attribute (e.g. ``self.x = y``).

    The receiver chain is not preserved by pypeeker's binder, so the target
    is the bare ``<unresolved>.<attr>`` symbol_id.
    """

    target: str
    line: int


@dataclass(frozen=True)
class ImpureBuiltinCall:
    """The function calls a known-impure builtin (``print``, ``open``, ...).

    Resolved via exact-name match against a denylist.
    """

    name: str
    line: int


@dataclass(frozen=True)
class AttributeMethodCall:
    """The function calls an attribute method whose tail name is on a denylist.

    For example ``os.system(cmd)`` produces method='system'. The base of the
    receiver is not preserved by pypeeker, so this is intentionally
    coarse-grained.

    `receiver_is_local_variable` lets check-layer policies decide whether
    this should count (e.g. purity ignores mutations of local variables but
    flags mutations of parameters).
    """

    method: str
    line: int
    receiver_is_local_variable: bool
