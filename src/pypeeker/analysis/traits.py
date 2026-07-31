"""The Trait registry: ``(value, confidence, provenance)``, registered per name.

A **Trait** is the analysis layer's answer to "what can we *say* about this
piece of the model" — the second of the four nouns in architecture.md's
target-architecture table (Model / Trait / Intent / Transaction). It pairs a
derived ``value`` with a :class:`~pypeeker.models.Confidence` (how reliable
the value is) and a human-readable ``provenance`` string naming which
analyzer derived it and from what facts.

A **trait provider** is a callable that derives a :class:`Trait` for one
anchor. This module settles on the pragmatic signature the current proof
migration (:mod:`pypeeker.analysis.variable_mutation`) actually consumes:

    ``(file_index: FileIndex, symbol_id: str) -> Trait``

i.e. one ``VARIABLE``/``FUNCTION``/... symbol inside one already-loaded
:class:`~pypeeker.models.FileIndex`. Nothing here generalizes beyond that —
a future trait needing project-wide context (the resolver, the symbol tree)
is free to define its own provider shape and its own registry key; this
module does not prescribe a universal anchor type.

:func:`register_trait` / :func:`get_trait_provider` mirror
:func:`pypeeker.refactor.registry.register_planner`: a provider registers
itself under a stable string name via the decorator, and a second
registration for the same name silently replaces the first (last import
wins) — the same precedence :func:`pypeeker.check.rules.register_rule`
gives *custom* rules among themselves. Unlike ``register_rule``, builtin
trait providers register through this same decorator rather than a
separate protected registry, so — unlike a builtin rule — a builtin trait
provider *can* be overridden by a consumer project. See
:func:`register_trait`'s own docstring for why that matters here (a trait
can back a refactor precondition, not just a check finding). This is
otherwise the same "drop in a module that registers itself" idiom
architecture.md's four-noun table calls out for rules and planners, now
extended to trait providers.

The point of a Trait living in :mod:`pypeeker.analysis` (importable by both
``check`` and ``refactor`` per ``import-boundaries``) is that a **rule** can
quantify a trait across every candidate in a file (∀ — "find all") while a
**precondition** verifies the same trait pointwise for one symbol just
before a plan commits (∃ one — "check this one"), without either package
duplicating the analysis that produces the trait's value. Two pairs are
proven: :mod:`pypeeker.analysis.variable_mutation` (``check.rules.prefer_tuple``
∀ / ``refactor.preconditions.NotReassigned`` pointwise) and
:mod:`pypeeker.analysis.type_annotation` (``prefer_tuple`` ∀ /
``refactor.preconditions.InferredListBinding`` pointwise, the first pair whose
two halves guard the *same* remedy end-to-end). The second pair adopted the
provider signature above unchanged, which is what turned it from "the shape
the one proof migration needed" into a validated contract. Which further
confidence computations should — and deliberately should not — become traits
is decided by the promotion rule in architecture.md (structural item 6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pypeeker.models import Confidence, FileIndex


@dataclass(frozen=True)
class Trait:
    """One derived fact about an anchor: a value, its confidence, and its provenance.

    ``value`` is intentionally untyped (``Any``) — different traits carry
    different shapes (a bool pair, an enum, a small frozen result object);
    callers know what they registered/expect for a given trait name.
    ``confidence`` is a :class:`~pypeeker.models.Confidence` level, the same
    vocabulary used throughout the model (``DECLARED``/``INFERRED``/
    ``HEURISTIC``/``UNKNOWN``). ``provenance`` is a human-readable string
    naming the analyzer that derived the value and the facts it read —
    useful for debugging and for an LLM consumer deciding how much to trust
    a finding built on top of it.

    **Provenance format convention.** Every builtin provider writes three
    parts, in this order::

        "<provider module dotted path>: <the facts read> for '<anchor id>' in <file path>"

    i.e. *producer* / *evidence* / *anchor*, as prose. Both proven pairs
    follow it — ``"pypeeker.analysis.variable_mutation: WRITE/CALL/READ
    references to 'm:f:x' in m.py"`` and ``"pypeeker.analysis.type_annotation:
    recorded type annotation of 'm:f:x' in m.py"``. The middle part is free
    prose in whatever wording fits the analyzer; only the opening module path
    and the presence of the anchor id are conventional, and that is all
    ``tests/test_traits.py::TestProvenanceConvention`` asserts.

    Explicitly **not** standardized: there is no structured type, no parser,
    no machine-readable format, and no schema version. Provenance is a
    debugging aid, not data.

    **Guardrail: provenance is never serialized into output.** It must not
    appear in CLI JSON, in a :class:`~pypeeker.check.models.Violation`
    message, or in a refactor precondition's refusal ``reason``. The moment
    it reaches an output surface it becomes a frozen contract that every
    future provider has to honour byte-for-byte — which is precisely the
    over-engineering this loose convention exists to avoid. Consumers read it
    in a debugger or a log, never through the CLI.
    """

    value: Any
    confidence: Confidence
    provenance: str


TraitProvider = Callable[[FileIndex, str], Trait]
"""``(file_index, symbol_id) -> Trait`` — see the module docstring for why
this is the anchor shape rather than something more general."""

_REGISTRY: dict[str, TraitProvider] = {}


def register_trait(name: str) -> Callable[[TraitProvider], TraitProvider]:
    """Register a trait provider under ``name`` (decorator).

    Mirrors :func:`pypeeker.refactor.registry.register_planner`: a second
    registration for the same ``name`` replaces the first (last import
    wins) rather than raising — the same precedence
    :func:`pypeeker.check.rules.register_rule` gives *custom* rules among
    themselves. Unlike ``register_rule``, there is no separate builtin
    registry here: builtin trait providers (e.g.
    :mod:`pypeeker.analysis.variable_mutation`) register through this same
    decorator into the same table, so a consumer project genuinely can
    override a builtin trait provider by importing a module that
    re-registers its name — ``register_rule`` explicitly forbids this for
    builtin rules (builtins win on a name clash), but no such guard exists
    here. That matters because a trait can back a refactor precondition,
    not just a check finding: ``variable-mutation`` is read by
    :class:`~pypeeker.refactor.preconditions.NotReassigned`, so a plugin
    that re-registers it changes whether inline-variable refuses to inline
    a reassigned binding.
    """

    def _decorate(provider: TraitProvider) -> TraitProvider:
        _REGISTRY[name] = provider
        return provider

    return _decorate


def get_trait_provider(name: str) -> TraitProvider | None:
    """Look up the trait provider registered under ``name``, or ``None`` on a miss."""
    return _REGISTRY.get(name)
