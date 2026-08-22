"""The expression DSL: selections, predicates, evidence, provenance, mutation terminals.

Phases 2-4 of the rewrite recorded in ``dsl-rewrite.md``. The **read half**
answers questions about the model — universes, predicates, the evidence
lattice, provenance. The **write half** (:mod:`pypeeker.dsl.terminals`,
:mod:`pypeeker.dsl.demotion`) names the repair a row earns as an
:class:`~pypeeker.intents.Intent` and hands it to the existing batch
machinery unchanged. The package reads ``models``/``analysis``, the query-side
substrate and ``intents``; it must never import ``check`` or ``refactor`` —
producing an intent is saying *what* should change, and only a planner on the
far side of that boundary knows *how*.

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
from pypeeker.dsl.columns import (
    COLUMNS,
    DEFINITION_ID,
    DEFINITION_ID_MODULE,
    DEFINITION_KIND,
    DEFINITION_MODULE,
    DEFINITION_NAME,
    DEFINITION_VISIBILITY,
    USAGE_ORIGINS,
    ProjectColumn,
    column_of,
)
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.demotion import (
    DEMOTE_ORIGIN_CLI,
    DEMOTION_RULES,
    demote_selection,
    privatize_selections,
)
from pypeeker.dsl.differential_fix import RepairSet, collect_repairs
from pypeeker.dsl.errors import (
    AmbiguousAnchorError,
    AnchorError,
    DerivedFieldError,
    DslError,
    MutationPreconditionError,
    OpaquePredicateError,
    ReachError,
    TraitShadowError,
    UnknownExpressionError,
    UnknownFactError,
    UnknownFieldError,
    UnknownFollowError,
    UnknownTraitError,
    UnknownUniverseError,
    UnplannableMutationError,
    UnresolvedAnchorError,
)
from pypeeker.dsl.evidence import CONFIDENCE_RANK, Derivation, meet
from pypeeker.dsl.expr import (
    FACT_READ_PREFIX,
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
    FactResolver,
    FieldRead,
    Not,
    Opaque,
    TraitAccess,
    TraitAssertion,
    TraitRead,
    Weaken,
    all_of,
    any_of,
    not_,
    opaque,
    row,
    trait_of,
    weakened_when,
)
from pypeeker.dsl.joins import (
    SET_READ_PREFIX,
    CorpusSet,
    ProjectedSet,
    SemiJoin,
    corpus_set,
    in_set,
    projected_set,
)
from pypeeker.dsl.facts import (
    Fact,
    FactAccess,
    FactRead,
    FactRow,
    FactTable,
    fact_of,
    fact_source,
    fact_specs,
    lazy_table,
    mapping_table,
)
from pypeeker.dsl.impurity import (
    import_time_builtin_call,
    import_time_module_call,
    import_time_project_call,
    pure_decorator_contracts,
)
from pypeeker.dsl.library import (
    EXPRESSIONS,
    TUPLE_CANDIDATE,
    expression,
    install_expressions,
)
from pypeeker.dsl.match import Match
from pypeeker.dsl.mutation import (
    allow_by_id,
    allow_by_id_or_module,
    argument_attribute_write,
    argument_mutator_call,
    argument_subscript_write,
    global_import_attribute_write,
    global_mutator_call,
    global_outer_scope_write,
    global_rebind,
)
from pypeeker.dsl.naming import trait
from pypeeker.dsl.provenance import (
    PROVENANCE_SCHEMA,
    UNMATCHED_JSON,
    derivation_document,
    derivation_to_dict,
)
from pypeeker.dsl.reach import Reach, join
from pypeeker.dsl.rules import (
    RULES,
    DslRule,
    Finding,
    MultiPartRule,
    PortedRule,
    Remediation,
    RulePart,
    dsl_rule,
)
from pypeeker.dsl.selection import (
    Application,
    Selection,
    imports,
    modules,
    references,
    scopes,
    symbols,
)
from pypeeker.dsl.terminals import (
    BELOW_FLOOR,
    DELETE_SYMBOL,
    DEMOTE,
    MUTATIONS,
    PLANNED_KINDS,
    REMOVE_IMPORT,
    RENAME_DOCSTRING_PARAM,
    REWRITE_STAR_IMPORT,
    TUPLIFY,
    FromAnchor,
    FromRow,
    Mutation,
    MutationDecision,
    Precondition,
)
from pypeeker.dsl.universes import UNIVERSE_NAMES, universe_fields, universe_follows
from pypeeker.dsl.visibility import (
    ACCESS_TEST_GLOBS,
    BARREL_EXPORTS,
    BASELINE_NAMESPACES,
    DEFAULT_TEST_GLOBS,
    DYNAMIC_ACCESS_WEAKENED_RULES,
    DYNAMIC_ACCESS_WEAKENING,
    DYNAMIC_MODULES,
    MODULE_FILES,
    RECORDED_PUBLIC_SYMBOLS,
    REFERENCED,
    born_private,
    over_exposed_export,
    over_exposed_module_symbol,
    test_only_production_code,
    under_exposed_access_from_tests,
    under_exposed_access_outside,
    unused_public_symbol,
)

__all__ = [
    # errors (loud, structured, every message naming the alternatives)
    "AmbiguousAnchorError",
    "AnchorError",
    "DerivedFieldError",
    "DslError",
    "OpaquePredicateError",
    "ReachError",
    "TraitShadowError",
    "UnknownExpressionError",
    "UnknownFactError",
    "UnknownFieldError",
    "UnknownFollowError",
    "UnknownTraitError",
    "UnknownUniverseError",
    "UnplannableMutationError",
    "UnresolvedAnchorError",
    "MutationPreconditionError",
    # anchors (evidence-typed, loudly resolved)
    "MAX_ANCHOR_CANDIDATES",
    "Anchor",
    "AnchorKind",
    "resolve_symbol_anchor",
    # the substrate a selection runs against
    "Corpus",
    # project columns: pointwise facts only the whole corpus can answer
    "COLUMNS",
    "DEFINITION_ID",
    "DEFINITION_ID_MODULE",
    "DEFINITION_KIND",
    "DEFINITION_MODULE",
    "DEFINITION_NAME",
    "DEFINITION_VISIBILITY",
    "USAGE_ORIGINS",
    "ProjectColumn",
    "column_of",
    # semi-joins: one projected id column, materialized once per run
    "SET_READ_PREFIX",
    "CorpusSet",
    "ProjectedSet",
    "SemiJoin",
    "corpus_set",
    "in_set",
    "projected_set",
    # selections over the five universes
    "UNIVERSE_NAMES",
    "Application",
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
    # the primitive fact tier: fork #11's hand-written sweeps, and the
    # configuration-shaped row source the model cannot supply
    "Fact",
    "FactAccess",
    "FactRead",
    "FactResolver",
    "FactRow",
    "FactTable",
    "fact_of",
    "fact_source",
    "fact_specs",
    "lazy_table",
    "mapping_table",
    # the builtin composed expressions, installed explicitly
    "EXPRESSIONS",
    "TUPLE_CANDIDATE",
    "expression",
    "install_expressions",
    # the runnable fix surface the differential oracle grades: every repair the
    # ported rules propose over one corpus, with the finding each came from
    "RepairSet",
    "collect_repairs",
    # rules as expressions: the new engine's rule library, graded per rule by
    # scripts/differential-check.py against the frozen old engine
    "RULES",
    "DslRule",
    "Finding",
    "MultiPartRule",
    "PortedRule",
    "Remediation",
    "RulePart",
    "dsl_rule",
    # mutation terminals: the write half. One named value per operation,
    # carrying its own confidence floor (fork #2); the application operator
    # takes no options and yields intents into the existing batch machinery.
    "BELOW_FLOOR",
    "DELETE_SYMBOL",
    "DEMOTE",
    "MUTATIONS",
    "PLANNED_KINDS",
    "REMOVE_IMPORT",
    "RENAME_DOCSTRING_PARAM",
    "REWRITE_STAR_IMPORT",
    "TUPLIFY",
    "FromAnchor",
    "FromRow",
    "Mutation",
    "MutationDecision",
    "Precondition",
    # demote/privatize: two selections over the one shared DEMOTE value
    "DEMOTE_ORIGIN_CLI",
    "DEMOTION_RULES",
    "demote_selection",
    "privatize_selections",
    # the visibility / reference-counting family: its shared projected sets,
    # the dynamic-access weakening and the inventory of rules it applies to
    "ACCESS_TEST_GLOBS",
    "BARREL_EXPORTS",
    "BASELINE_NAMESPACES",
    "DEFAULT_TEST_GLOBS",
    "DYNAMIC_ACCESS_WEAKENED_RULES",
    "DYNAMIC_ACCESS_WEAKENING",
    "DYNAMIC_MODULES",
    "MODULE_FILES",
    "RECORDED_PUBLIC_SYMBOLS",
    "REFERENCED",
    "born_private",
    "over_exposed_export",
    "over_exposed_module_symbol",
    "test_only_production_code",
    "under_exposed_access_from_tests",
    "under_exposed_access_outside",
    "unused_public_symbol",
    # the mutation family: the two `allow` shapes it needs and one builder per
    # frozen mutation shape
    "allow_by_id",
    "allow_by_id_or_module",
    "argument_attribute_write",
    "argument_mutator_call",
    "argument_subscript_write",
    "global_import_attribute_write",
    "global_mutator_call",
    "global_outer_scope_write",
    "global_rebind",
    # the impurity family: one builder per frozen arm of the pair
    "import_time_builtin_call",
    "import_time_module_call",
    "import_time_project_call",
    "pure_decorator_contracts",
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
    "Weaken",
    # authoring surface
    "FACT_READ_PREFIX",
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
    "weakened_when",
]
