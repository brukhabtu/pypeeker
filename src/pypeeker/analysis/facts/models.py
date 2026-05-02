"""Typed semantic facts produced by the fact extractors.

Facts are pure observations about a function's body — they say what the code
*does*, not whether that's good or bad. Composite checks (e.g. purity,
determinism, side-effects) consume facts and apply policy.

All fact types are frozen dataclasses to enable use in sets / dict keys and
to make accidental mutation impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
class ModuleCall:
    """A fully-qualified call into an imported module.

    Computed when an attribute call's receiver root resolves to an IMPORT
    symbol — we combine ``imported_from + chain[1:] + leaf`` into a
    canonical name like ``os.system`` or ``pathlib.Path.write_text``.
    """

    full_name: str
    line: int


class ReceiverKind(str, Enum):
    """How the receiver of an attribute call resolves.

    Drives check-layer policy: a parameter mutation is caller-visible
    (impure), a local variable mutation is pure-local, an unknown receiver
    forces conservative classification.
    """

    IMPORT = "import"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    SELF = "self"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AttributeMethodCall:
    """A method call on an attribute receiver.

    The receiver_kind tells the check layer how to interpret this:
    parameter mutations are caller-visible; local-variable mutations are
    pure-local; unknown receivers (dynamic chains, unresolved roots) force
    conservative classification.

    receiver_type, when set, is the bare type name (e.g. 'Path', 'IO',
    'Logger') derived from the receiver root's annotation. Checks may use
    it to apply a type-specific denylist instead of the generic
    receiver-kind dispatch.
    """

    method: str
    line: int
    receiver_kind: ReceiverKind
    receiver_type: str | None = None
