"""Loud, structured errors for the expression DSL.

Fork #12 of ``dsl-rewrite.md`` resolves anchor lookups to **loud structured
errors, never empty results** — a lookup that finds nothing or finds too much
raises something a caller can serialize, not an empty selection a caller has
to guess about. This module generalizes that discipline to every authoring
mistake the DSL can detect: an unknown field, an unknown follow step, an
unknown expression name, an unknown trait, a bare-callable predicate, a
misplaced project-reach expression.

Two properties every error here holds to:

* **It carries a machine code.** :attr:`DslError.code` is a stable hyphenated
  string, and :meth:`DslError.as_error_fields` returns a dict shaped for
  ``cli.py``'s ``_emit_error(code, message, **extra)`` sink, so a refusal
  crosses the CLI boundary without a translation table in between.
* **Its message names the valid alternatives.** "unknown field 'kindd'" is a
  dead end; "unknown field 'kindd' for the symbols universe; valid fields are
  ..." is a fix. Every constructor below takes the candidate set and puts it
  in the message. This is the whole reason the errors are loud rather than
  silent: an LLM consumer cannot attach a debugger, so the refusal has to
  carry its own remedy.

Nothing here is raised *during row evaluation*. Evaluation is deliberately
total — a field the row does not carry reads as ``None`` at
:attr:`~pypeeker.models.Confidence.UNKNOWN` so downstream predicates simply go
false, matching the contract ``pypeeker.analysis``'s builtin trait providers
already hold to (a provider must not raise on an unknown ``symbol_id``).
Loudness lives at *resolution* and *authoring* time, where a mistake is a bug
in the expression rather than a gap in the index.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _listed(names: Iterable[str]) -> str:
    """Render ``names`` as a sorted, comma-separated list for an error message."""
    rendered = sorted(str(name) for name in names)
    return ", ".join(rendered) if rendered else "(none)"


class DslError(Exception):
    """Base class for every structured refusal the DSL raises.

    Subclasses set :attr:`code` and pass any extra structured payload to
    :meth:`__init__` as keyword arguments; those land verbatim in
    :meth:`as_error_fields` alongside ``code`` and ``message``.
    """

    code = "dsl-error"

    def __init__(self, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.message = message
        self.fields: Mapping[str, Any] = dict(fields)

    def as_error_fields(self) -> dict[str, Any]:
        """Return ``{"code", "message", **extra}`` for the CLI's error sink.

        Shaped so a caller can splat it: ``_emit_error(**err.as_error_fields())``.
        """
        return {"code": self.code, "message": self.message, **self.fields}


class UnknownFieldError(DslError):
    """A field name that is not readable where it was written.

    ``declared_by`` names an opaque predicate when the unreadable name came
    from its ``reads=`` declaration rather than from a ``row.<name>`` node.
    The extra sentence matters because that case has no other symptom: the
    body would read the field as ``None`` and go false for every row, which
    looks exactly like an honest empty answer.
    """

    code = "unknown-field"

    def __init__(
        self,
        field: str,
        valid: Iterable[str],
        *,
        universe: str | None = None,
        declared_by: str | None = None,
    ) -> None:
        valid = tuple(sorted(str(name) for name in valid))
        where = f" for the {universe} universe" if universe else ""
        fields: dict[str, Any] = {"field": field, "valid": list(valid)}
        source = ""
        if declared_by is not None:
            fields["declared_by"] = declared_by
            source = (
                f". Opaque predicate {declared_by!r} declares it in reads=, but an "
                f"earlier project() dropped it, so the body would read it as None and "
                f"reject every row silently. Move the where() before the project(), or "
                f"keep the field visible."
            )
        super().__init__(
            f"unknown field {field!r}{where}; valid fields are: {_listed(valid)}{source}",
            **fields,
        )
        self.field = field
        self.valid = valid
        self.declared_by = declared_by


class UnknownTraitError(DslError):
    """A trait name with no provider registered under it.

    Raised rather than silently contributing
    :attr:`~pypeeker.models.Confidence.UNKNOWN`: a trait the expression names
    but the registry does not have is an authoring mistake, and swallowing it
    would make every predicate built on that trait quietly false.
    """

    code = "unknown-trait"

    def __init__(self, trait: str, valid: Iterable[str]) -> None:
        valid = tuple(sorted(str(name) for name in valid))
        super().__init__(
            f"no trait provider is registered under {trait!r}; "
            f"traits available here are: {_listed(valid)}. Register one with "
            f"pypeeker.analysis.register_trait, or name an expression with "
            f"pypeeker.dsl.trait().",
            trait=trait,
            valid=list(valid),
        )
        self.trait = trait
        self.valid = valid


class UnknownUniverseError(DslError):
    """A universe name outside the five fork #8 fixes.

    Separate from :class:`UnknownFieldError` on purpose: "there is no such
    universe" and "that universe has no such field" are different mistakes and
    want different remedies, and collapsing them would send a caller hunting
    for a field name when the problem is the noun.
    """

    code = "unknown-universe"

    def __init__(self, universe: str, valid: Iterable[str]) -> None:
        valid = tuple(str(name) for name in valid)
        super().__init__(
            f"unknown universe {universe!r}; the universes are: {_listed(valid)}",
            universe=universe,
            valid=list(valid),
        )
        self.universe = universe
        self.valid = valid


class UnknownFollowError(DslError):
    """A ``follow()`` step the current universe does not offer."""

    code = "unknown-follow"

    def __init__(self, step: str, valid: Iterable[str], *, universe: str | None = None) -> None:
        valid = tuple(sorted(str(name) for name in valid))
        where = f" from the {universe} universe" if universe else ""
        super().__init__(
            f"unknown follow step {step!r}{where}; valid steps are: {_listed(valid)}",
            step=step,
            valid=list(valid),
        )
        self.step = step
        self.valid = valid


class UnknownExpressionError(DslError):
    """A named expression that is not registered."""

    code = "unknown-expression"

    def __init__(self, expression: str, valid: Iterable[str]) -> None:
        valid = tuple(sorted(str(name) for name in valid))
        super().__init__(
            f"unknown expression {expression!r}; registered expressions are: {_listed(valid)}",
            expression=expression,
            valid=list(valid),
        )
        self.expression = expression
        self.valid = valid


class OpaquePredicateError(DslError):
    """A predicate that is opaque without declaring what it reads.

    Fork #9: ``where()`` rejects a bare callable outright, and the sanctioned
    escape — :func:`pypeeker.dsl.opaque` — demands a ``reads=`` declaration.
    An opaque with no declared reads is not inspectable, which is the entire
    thing the escape hatch exists to preserve.
    """

    code = "opaque-predicate"


class ReachError(DslError):
    """An expression used somewhere its derived reach does not fit.

    The motivating case is :func:`pypeeker.dsl.trait`: the registry's provider
    signature is ``(FileIndex, symbol_id) -> Trait``, so an expression whose
    reach is ``PROJECT`` has no substrate to run against. See architecture.md's
    trait-promotion rule, clause (b).
    """

    code = "reach-refused"


class TraitShadowError(DslError):
    """A named expression that would displace a provider this package did not create.

    :mod:`pypeeker.analysis.traits` is deliberately last-registration-wins with
    no builtin guard, because a consumer project genuinely may want to override
    a builtin trait provider. That same leniency makes it possible to silently
    replace ``type-annotation`` by naming an expression after it — and a trait
    backs refactor preconditions, not just findings, so the blast radius of an
    accidental shadow is wider than a wrong message. :func:`pypeeker.dsl.trait`
    therefore refuses unless the caller passes ``replace=True``. Re-registering
    a provider :func:`~pypeeker.dsl.trait` itself created is not shadowing and
    is always allowed, which is what makes installation idempotent.
    """

    code = "trait-shadowed"

    def __init__(self, name: str) -> None:
        super().__init__(
            f"a trait provider is already registered under {name!r} and it was not "
            f"created by pypeeker.dsl.trait(). Naming an expression after it would "
            f"replace it for every consumer in this process, refactor preconditions "
            f"included. Choose another name, or pass replace=True to state that the "
            f"displacement is deliberate.",
            trait=name,
        )
        self.trait = name


class AnchorError(DslError):
    """Base class for anchor lookups that cannot yield exactly one anchor."""

    code = "anchor-error"

    def __init__(self, message: str, *, anchor: str, candidates: Iterable[str]) -> None:
        candidates = tuple(candidates)
        super().__init__(message, anchor=anchor, candidates=list(candidates))
        self.anchor = anchor
        self.candidates = candidates


class UnresolvedAnchorError(AnchorError):
    """No symbol the selection could produce matched the requested anchor.

    Two distinct situations share the ``unresolved-anchor`` code, because both
    mean "this anchor yields no row", and they are told apart by the message
    rather than by a second code:

    * Nothing like it is indexed at all — the plain "no symbol matches", with
      near misses attached when the tail ladder found any.
    * A symbol with exactly this id *is* indexed, outside the configured source
      roots (``outside_source_roots=True``). Saying "no symbol matches" there
      would be a false statement about the index, and would leave the caller
      with nothing to change; naming the source roots names the fix.
    """

    code = "unresolved-anchor"

    def __init__(
        self,
        anchor: str,
        candidates: Iterable[str],
        *,
        outside_source_roots: bool = False,
    ) -> None:
        candidates = tuple(candidates)
        if outside_source_roots:
            message = (
                f"anchor {anchor!r} names an indexed symbol that lies outside the "
                f"configured source roots, so no selection reaches it; add its "
                f"directory to [tool.pypeeker] src, or anchor on a symbol inside "
                f"the roots"
            )
        else:
            hint = f"; did you mean: {_listed(candidates)}" if candidates else ""
            message = f"no symbol matches anchor {anchor!r}{hint}"
        super().__init__(message, anchor=anchor, candidates=candidates)


class AmbiguousAnchorError(AnchorError):
    """More than one symbol matched the requested anchor."""

    code = "ambiguous-anchor"

    def __init__(self, anchor: str, candidates: Iterable[str]) -> None:
        candidates = tuple(candidates)
        super().__init__(
            f"anchor {anchor!r} is ambiguous; it matches: {_listed(candidates)}. "
            f"Pass one of these ids instead.",
            anchor=anchor,
            candidates=candidates,
        )
