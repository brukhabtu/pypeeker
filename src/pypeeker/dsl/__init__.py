"""The expression DSL: selections, predicates, evidence, provenance. Read-only.

Phase 2 of the rewrite recorded in ``dsl-rewrite.md`` — **the read half**.
Everything in this package answers questions about the model; there are no
mutation terminals here, and none arrive until phase 4. The package reads
``models``/``analysis`` and the query-side substrate; it must never import
``check`` or ``refactor``.

Two things this package deliberately does **not** own:

* **A trait registry.** Naming an expression with :func:`trait` registers a
  provider into the one that already exists,
  :mod:`pypeeker.analysis.traits` — there is no second table. Primitive
  traits stay hand-written Python declaring their own confidence (fork #11);
  composing one out of an expression is an addition to that registry, not a
  replacement for it.
* **A provenance format.** :attr:`pypeeker.analysis.Trait.provenance` is
  unstructured debugging prose that must never reach output. The derivation
  tree this package builds is a different object: versioned, structured,
  additive-only, and meant for ``--why``. No provenance string is ever copied
  into a derivation.

Everything public is re-exported here. That is not decoration: pypeeker's own
``unused-public-symbol`` and ``over-exposed-module-symbol`` rules exempt names
a package's ``__init__`` barrel re-exports, and ``barrel-only`` requires
consumers outside this package to import through here rather than reaching
into a submodule.
"""

from pypeeker.dsl.anchors import (
    MAX_ANCHOR_CANDIDATES,
    Anchor,
    AnchorKind,
    resolve_symbol_anchor,
)
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import (
    AmbiguousAnchorError,
    AnchorError,
    DslError,
    OpaquePredicateError,
    ReachError,
    TraitShadowError,
    UnknownExpressionError,
    UnknownFieldError,
    UnknownFollowError,
    UnknownTraitError,
    UnknownUniverseError,
    UnresolvedAnchorError,
)
from pypeeker.dsl.evidence import CONFIDENCE_RANK, Derivation, meet
from pypeeker.dsl.expr import (
    FIELD_READ_PREFIX,
    PROJECT_READ_PREFIX,
    TRAIT_READ_PREFIX,
    UNMATCHED,
    AllOf,
    AnyOf,
    Attr,
    Compare,
    Const,
    EvalContext,
    Expr,
    FieldRead,
    Not,
    Opaque,
    TraitAccess,
    TraitAssertion,
    TraitRead,
    all_of,
    any_of,
    not_,
    opaque,
    row,
    trait_of,
)
from pypeeker.dsl.library import (
    EXPRESSIONS,
    TUPLE_CANDIDATE,
    expression,
    install_expressions,
)
from pypeeker.dsl.naming import trait
from pypeeker.dsl.provenance import (
    PROVENANCE_SCHEMA,
    UNMATCHED_JSON,
    derivation_document,
    derivation_to_dict,
)
from pypeeker.dsl.reach import Reach, join
from pypeeker.dsl.rules import RULES, DslRule, Finding, dsl_rule
from pypeeker.dsl.selection import (
    Match,
    Selection,
    imports,
    modules,
    references,
    scopes,
    symbols,
)
from pypeeker.dsl.universes import UNIVERSE_NAMES, universe_fields, universe_follows

__all__ = [
    # errors (loud, structured, every message naming the alternatives)
    "AmbiguousAnchorError",
    "AnchorError",
    "DslError",
    "OpaquePredicateError",
    "ReachError",
    "TraitShadowError",
    "UnknownExpressionError",
    "UnknownFieldError",
    "UnknownFollowError",
    "UnknownTraitError",
    "UnknownUniverseError",
    "UnresolvedAnchorError",
    # anchors (evidence-typed, loudly resolved)
    "MAX_ANCHOR_CANDIDATES",
    "Anchor",
    "AnchorKind",
    "resolve_symbol_anchor",
    # the substrate a selection runs against
    "Corpus",
    # selections over the five universes
    "UNIVERSE_NAMES",
    "Match",
    "Selection",
    "imports",
    "modules",
    "references",
    "scopes",
    "symbols",
    "universe_fields",
    "universe_follows",
    # evidence lattice
    "CONFIDENCE_RANK",
    "Derivation",
    "meet",
    # reach (derived, never declared)
    "Reach",
    "join",
    # naming an expression: registration into analysis/traits.py's registry
    "trait",
    # the builtin composed expressions, installed explicitly
    "EXPRESSIONS",
    "TUPLE_CANDIDATE",
    "expression",
    "install_expressions",
    # rules as expressions: the new engine's rule library, graded per rule by
    # scripts/differential-check.py against the frozen old engine
    "RULES",
    "DslRule",
    "Finding",
    "dsl_rule",
    # --why: the derivation tree as versioned, additive-only JSON
    "PROVENANCE_SCHEMA",
    "UNMATCHED_JSON",
    "derivation_document",
    "derivation_to_dict",
    # expression nodes
    "AllOf",
    "AnyOf",
    "Attr",
    "Compare",
    "Const",
    "EvalContext",
    "Expr",
    "FieldRead",
    "Not",
    "Opaque",
    "TraitAccess",
    "TraitAssertion",
    "TraitRead",
    # authoring surface
    "FIELD_READ_PREFIX",
    "PROJECT_READ_PREFIX",
    "TRAIT_READ_PREFIX",
    "UNMATCHED",
    "all_of",
    "any_of",
    "not_",
    "opaque",
    "row",
    "trait_of",
]
