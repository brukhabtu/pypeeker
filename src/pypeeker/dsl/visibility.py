"""The visibility / reference-counting rule family, as expressions.

Phase 3b of ``dsl-rewrite.md``, extended by phase 3d. Six rules that all ask one
question — *who actually uses this?* — and answer it by counting references
across the whole corpus rather than by reading one file:

* ``unused-public-symbol`` — nothing anywhere references it.
* ``over-exposed-module-symbol`` — only its own module references it.
* ``over-exposed-export`` — only its own package references the definition a
  barrel re-exports.
* ``born-private`` — the same module-local test, applied prospectively against
  a recorded baseline.
* ``test-only-production-code`` — only test files reference it.
* ``under-exposed-access`` — a module *other than* the defining one references
  something underscore-private (phase 3d).

The sixth is the family's odd one in two ways, both consequences of it
quantifying over **reference sites** rather than over definitions: it is the
one rule here whose selection starts at :func:`~pypeeker.dsl.references`, and
the one that carries no :data:`DYNAMIC_ACCESS_WEAKENING` — its frozen body is
not a caller of the shared confidence helper. It shares
:data:`MODULE_FILES`, :func:`_as_str_list`, :func:`_test_path_clause` and
the ``allow`` pattern contract (:func:`_allow_clause`) with the other five, and it lives here because
its frozen source lived beside two of them in the deleted ``check`` engine's
visibility module.

What the family needed from the DSL
-----------------------------------

Nothing rule-shaped. Four orthogonal primitives, all of them in
:mod:`pypeeker.dsl.joins`, :mod:`pypeeker.dsl.columns` and
:mod:`pypeeker.dsl.expr`, and all of them phase-2 surface this module merely
uses: a semi-join against a corpus-wide projected column
(:func:`~pypeeker.dsl.in_set`), pointwise project columns
(:func:`~pypeeker.dsl.column_of`), evidence weakening
(:func:`~pypeeker.dsl.weakened_when`), and comparisons whose right-hand side is
another expression. Every rule below is a conjunction of clauses in the frozen
engine's own written order; there is no bespoke Python deciding what fires.

The shared candidate
--------------------

Four of the five open with the same nine clauses — :func:`_candidate_clauses`
— because the frozen rules open with the same nine ``continue`` statements.
Written in that order deliberately (fork #3 makes written order normative), so
the expression reads against its source.

That prefix ends with the **barrel exemption**, which is
``dsl-rewrite.md``'s named convergence: *the barrel exemption is a semi-join on
one projected id column, materialized once per run*. It is
:data:`BARREL_EXPORTS` here, one ``ProjectedSet``, shared by every rule in the
file so the corpus is scanned for it once however many of the five run.

Why the key is ``row.symbol_id`` and not a canonical-id column
--------------------------------------------------------------

The frozen rules test ``resolver.resolve_definition(symbol.symbol_id)`` against
the barrel set. This module tests ``row.symbol_id`` directly. Those agree
because ``resolve_definition`` returns its argument unchanged for anything that
is not an ``IMPORT`` (``resolve.py``'s chain walk returns immediately), and
every candidate here is a ``FUNCTION``, ``CLASS`` or ``VARIABLE``. Measured on
this repository: equal for all of them. Dropping the column keeps the
expression readable and saves a resolver call per row; ``over-exposed-export``,
whose subject *is* an import, uses :data:`~pypeeker.dsl.DEFINITION_ID` properly.

The library-mode ``protected`` clause
-------------------------------------

The frozen rules each carry a final ``canonical in protected`` skip, where
``protected`` is ``_public_root_protected`` — the ids re-exported by a barrel
under a public root, in library mode only. It is **omitted from the four
symbol-side rules here**, because it is a subset of the barrel exemption by
construction (both are ``resolve_definition`` of the ``IMPORT`` symbols in an
``__init__.py``; protected merely filters those barrels by root), and all four
test barrel membership first. The frozen docstring says as much itself:
"today subsumed by the unconditional barrel exemption above". Omitting a
provably unreachable clause is behaviour-preserving, and it keeps the
``[tool.pypeeker.visibility]`` policy object out of this package.
``over-exposed-export`` has no unconditional barrel exemption — its subject is
a barrel export — so it implements ``protected`` properly, in
:func:`_protected_exports`.

The visibility table is read raw, not parsed
--------------------------------------------

:func:`_visibility_table` and friends read the **raw**
``[tool.pypeeker.visibility]`` mapping the config loader injects into every
enabled rule's options, and deliberately refuse a parsed
:class:`~pypeeker.project.VisibilityConfig` with a :exc:`TypeError`. That is
not a layering workaround — ``dsl`` may import ``project``, and
:func:`pypeeker.dsl.config.read_config` does. It is the contract: the injected
value is the raw table on both engines, so anything that hands these rules a
parsed object has already diverged from what the old engine sees, and the
loud refusal is what catches it.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from typing import Any

from pypeeker.dsl.columns import (
    DEFINITION_ID,
    DEFINITION_ID_MODULE,
    DEFINITION_KIND,
    DEFINITION_MODULE,
    DEFINITION_NAME,
    DEFINITION_VISIBILITY,
    USAGE_ORIGINS,
)
from pypeeker.dsl.columns import column_of
from pypeeker.dsl.config import as_str_list
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.expr import (
    Const,
    Expr,
    all_of,
    allow_patterns,
    any_of,
    not_,
    opaque,
    row,
    weakened_when,
)
from pypeeker.dsl.joins import ProjectedSet, corpus_set, in_set, projected_set
from pypeeker.dsl.selection import Selection, references, symbols
from pypeeker.models import (
    Confidence,
    ReferenceKind,
    SymbolKind,
    Visibility,
    builtin_id,
)
from pypeeker.storage import baseline_namespaces, baseline_path, load_symbol_baseline

UNUSED_PUBLIC_SYMBOL = "unused-public-symbol"
OVER_EXPOSED_MODULE_SYMBOL = "over-exposed-module-symbol"
OVER_EXPOSED_EXPORT = "over-exposed-export"
BORN_PRIVATE = "born-private"
TEST_ONLY_PRODUCTION_CODE = "test-only-production-code"
# under-exposed-access deliberately has no constant here: the five above exist
# only because DYNAMIC_ACCESS_WEAKENED_RULES enumerates them, and that rule is
# not a member. Its id is a literal in pypeeker.dsl.rules, like every other.

DYNAMIC_ACCESS_WEAKENED_RULES: frozenset[str] = frozenset({
    UNUSED_PUBLIC_SYMBOL,
    OVER_EXPOSED_MODULE_SYMBOL,
    OVER_EXPOSED_EXPORT,
    BORN_PRIVATE,
    TEST_ONLY_PRODUCTION_CODE,
})
"""Exactly the rules the dynamic-access weakening applies to. Enumerated, not derived.

The frozen engine (deleted at the flip) called
``check.rules._dynamic_access_confidence`` from **four modules at five call
sites**, and the count is the point: its rules module weakened
``unused-public-symbol``; its visibility module weakened both
``over-exposed-module-symbol`` **and** ``over-exposed-export`` — two rules in
one module, so the second is exactly the site an inventory taken *by module*
would miss; its born-private module weakened ``born-private``; and its
test-only module weakened ``test-only-production-code``. No other caller
existed; the visibility module's third rule ``under-exposed-access`` did not
weaken, and neither does any other consumer of ``resolve_definition``.

This constant is the ported inventory of that call-site list, and
``tests/test_dsl_visibility_rules.py`` asserts that the rules whose built
selection contains a :class:`~pypeeker.dsl.Weaken` node are exactly these and
that no other rule in :data:`pypeeker.dsl.RULES` contains one — so "the same
rule set as today" is checked rather than claimed.
"""

_FUNCTION_OR_CLASS: tuple[SymbolKind, ...] = (SymbolKind.FUNCTION, SymbolKind.CLASS)

_KIND_CHOICES: tuple[SymbolKind, ...] = (
    SymbolKind.FUNCTION,
    SymbolKind.CLASS,
    SymbolKind.VARIABLE,
)
"""Kinds the ``kinds`` option may select; anything else is ignored (frozen contract)."""

_DEFAULT_KINDS: tuple[str, ...] = ("function", "class")

DEFAULT_TEST_GLOBS: tuple[str, ...] = ("tests/**", "test_*.py", "**/test_*.py")
"""``test-only-production-code``'s default ``test-globs``, verbatim from the frozen rule."""

ACCESS_TEST_GLOBS: tuple[str, ...] = (
    "tests/*",
    "*/tests/*",
    "test_*.py",
    "*/test_*.py",
    "*_test.py",
    "*/*_test.py",
    "conftest.py",
    "*/conftest.py",
)
"""``under-exposed-access``'s default ``test-globs``, verbatim from the frozen rule.

Deliberately **not** :data:`DEFAULT_TEST_GLOBS`. The two frozen rules ship two
different defaults — ``check.builtin.test_only_production_code`` has three
patterns rooted at ``tests/**``, ``check.builtin.visibility._DEFAULT_TEST_GLOBS``
has these eight — and the difference is observable rather than cosmetic: only
these classify ``*_test.py`` and ``conftest.py``, and only these match a
``test_*.py`` basename via ``*/test_*.py`` in a nested directory. Sharing one
constant because the names read alike would change which reach-ins get the
"accessed from tests" wording.
"""

_DYNAMIC_ACCESS_BUILTIN_IDS: tuple[str, ...] = tuple(
    builtin_id(name) for name in ("getattr", "globals", "vars", "locals")
)
"""Resolved builtin reference ids that signal dynamic symbol access."""

_SYMBOLS_KEY = "symbols"


# ---------------------------------------------------------------------------
# corpus-wide projected sets: the semi-join right-hand sides
# ---------------------------------------------------------------------------

BARREL_EXPORTS: ProjectedSet = projected_set(
    "barrel-exports",
    symbols()
    .where(all_of(row.file_path.matches("*__init__.py"), row.kind.eq(SymbolKind.IMPORT)))
    .follow("definition")
    .project("symbol_id"),
)
"""Canonical ids re-exported by a package ``__init__`` barrel: deliberate API surface.

``dsl-rewrite.md``'s convergence made literal — one projected id column,
materialized once per run, tested pointwise by :func:`~pypeeker.dsl.in_set`.
Shared by all five rules, so five semi-joins cost one scan.

Measured against the frozen engine's inline computation on this repository:
**279 ids on both sides, zero difference.**
"""

MODULE_FILES: ProjectedSet = projected_set(
    "module-files",
    symbols().where(row.kind.eq(SymbolKind.MODULE)).project("file_path"),
)
"""File paths the binder gave a MODULE symbol: the row-side port of the frozen
rules' ``module_id is None -> continue``.

A file gets no MODULE symbol when its module path is empty — a source root
that is itself a package makes ``paths.module_path_from`` return ``""`` for
its ``__init__.py``, and the binder skips emitting a MODULE symbol for an
empty module path. :func:`pypeeker.dsl.columns._modules_by_file` already
drops such a file on the project-column side; this is the same skip applied
to the candidate row itself, so the two stay in agreement (``dsl-rewrite.md``
ledger, phase 3b). It is a semi-join over the existing ``symbols()`` universe
rather than a change to what that universe produces, so
:func:`~pypeeker.dsl.universes._Env.of`'s file-path fallback keeps every
row's shape uniform for every other consumer — see that function's docstring
for the companion half of this reconciliation.
"""

REFERENCED: ProjectedSet = projected_set(
    "referenced",
    references().follow("definition").project("symbol_id"),
)
"""Canonical definition ids something in the corpus references. ``unused-public-symbol``'s test."""

DYNAMIC_MODULES: ProjectedSet = projected_set(
    "dynamic-access-modules",
    references().where(row.symbol_id.is_in(*_DYNAMIC_ACCESS_BUILTIN_IDS)).project("module"),
)
"""Modules referencing ``getattr``/``globals``/``vars``/``locals``. 8 on this repo, as the frozen engine finds."""

DYNAMIC_ACCESS_WEAKENING: Expr = weakened_when(
    in_set(row.module, DYNAMIC_MODULES), Confidence.HEURISTIC
)
"""The dynamic-access weakening, as one conjunct every rule in the family carries.

The port of ``check.rules._dynamic_access_confidence``: a finding about a
symbol defined in a module that reaches for ``getattr`` and friends is still
emitted, but its evidence is ``HEURISTIC``, because dynamic access can consume
(or serve) the symbol invisibly and reference counting cannot see it.

Reused unchanged by ``over-exposed-export``, whose row is the barrel's own
``IMPORT`` symbol and whose ``module`` field therefore *is* the package the
frozen rule passes there.
"""


# ---------------------------------------------------------------------------
# option coercion — mirrors the frozen engine's, silent drops included
# ---------------------------------------------------------------------------


def _as_str_list(raw: Any) -> tuple[str, ...]:
    """Coerce an option value to strings (``''`` / ``None`` / ``[]`` -> empty).

    :func:`pypeeker.dsl.config.as_str_list` — the one copy of the frozen
    ``check.rules._as_str_list`` — as a tuple, because nothing here mutates the
    result and a tuple is hashable.
    """
    return tuple(as_str_list(raw))


def _selected_kinds(raw: Any) -> tuple[SymbolKind, ...]:
    """Coerce the ``kinds`` option, **dropping** unknown and out-of-range values.

    ``check.builtin.visibility._selected_kinds``, silent drop included: an
    unparseable value is ignored rather than falling back to the default, and a
    parseable kind outside function/class/variable is ignored too. Returned
    sorted by value rather than as the frozen engine's ``frozenset``, so the
    written expression is deterministic across runs — the only consumer is
    :meth:`~pypeeker.dsl.Expr.is_in`, whose test is membership either way.
    """
    out: list[SymbolKind] = []
    for value in _as_str_list(raw) or _DEFAULT_KINDS:
        try:
            kind = SymbolKind(value)
        except ValueError:
            continue
        if kind in _KIND_CHOICES and kind not in out:
            out.append(kind)
    return tuple(sorted(out, key=lambda kind: kind.value))


def _visibility_table(options: Mapping[str, Any]) -> Mapping[str, Any]:
    """The raw ``[tool.pypeeker.visibility]`` table the run service injects, or empty.

    The frozen ``check.config.load_config`` copied the project-wide visibility section into
    *every* enabled rule's options under the reserved ``visibility`` key, and
    ``pypeeker.project.coerce_visibility`` parses it. That parse is tolerant —
    a missing table, an unknown ``mode``, non-list values all fall back to
    defaults — and so is this, for the same reason and with the same result.
    The slice is re-read here from the raw table so the read half's option
    handling stays inspectable in the grammar (see the module docstring).

    Only the raw mapping shape is accepted. ``coerce_visibility`` also takes an
    already-parsed ``VisibilityConfig``, but the DSL is fed the raw table by
    contract (``dsl.read_config`` injects it as ``check.config`` did), so a
    parsed config arriving here is a wiring mistake and refuses loudly rather
    than silently degrading to app-mode defaults.
    """
    raw = options.get("visibility")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise TypeError(
            f"visibility option must be the raw [tool.pypeeker.visibility] "
            f"mapping, got {type(raw).__name__}; a parsed VisibilityConfig "
            f"cannot cross into dsl (project is outside its import boundary)"
        )
    return raw


def _merged_allow_decorators(options: Mapping[str, Any]) -> tuple[str, ...]:
    """A rule's own ``allow-decorators`` followed by the global visibility list."""
    return _as_str_list(options.get("allow-decorators")) + _as_str_list(
        _visibility_table(options).get("allow-decorators")
    )


def _is_library(options: Mapping[str, Any]) -> bool:
    """True when the project declared ``mode = "library"``, exactly.

    Any other value — including a typo — is app mode, matching
    ``parse_visibility_config``'s ``if mode not in VISIBILITY_MODES: mode = "app"``.
    """
    return _visibility_table(options).get("mode") == "library"


# ---------------------------------------------------------------------------
# pattern clauses: fnmatch over a configured pattern list
# ---------------------------------------------------------------------------


def _has_allowed_decorator(decorators: Iterable[str], patterns: tuple[str, ...]) -> bool:
    """True when any decorator matches a pattern, by full text or leading callable.

    ``check.rules._has_allowed_decorator``. Decorators are stored as source
    text without the ``@`` (``register_rule("name", scope="project")``), so
    matching the head as well is what lets a plain name work without a
    trailing wildcard.
    """
    if not patterns:
        return False
    for decorator in decorators:
        head = decorator.split("(", 1)[0].strip()
        if any(
            fnmatch.fnmatchcase(decorator, pattern) or fnmatch.fnmatchcase(head, pattern)
            for pattern in patterns
        ):
            return True
    return False


def _allow_clause(patterns: tuple[str, ...]) -> Expr:
    """The ``allow`` option: a pattern fnmatches the symbol id or its module path.

    ``check.rules._matches_any`` and ``check.builtin.visibility._allowed`` are
    the same function under two names, and this is it, written in the grammar
    through :func:`~pypeeker.dsl.allow_patterns` rather than smuggled into an
    opaque — :meth:`~pypeeker.dsl.Expr.matches` *is* ``fnmatchcase``, so
    ``--why`` sees every pattern and every column. ``row.id_module`` **is** the
    ``split(":", 1)[0]`` both frozen functions perform; it is deliberately not
    ``row.module``, for the reason :func:`pypeeker.dsl.rules._matches_any`
    gives.
    """
    return allow_patterns(patterns, row.symbol_id, row.id_module)


def _decorator_clause(patterns: tuple[str, ...]) -> Expr:
    """The ``allow-decorators`` option as a declared-reads opaque over ``decorators``."""

    @opaque("allow-decorator", reads=("decorators",))
    def _decorated(record: Any) -> bool:
        return _has_allowed_decorator(record.decorators or (), patterns)

    return _decorated


def _test_path_clause(globs: tuple[str, ...]) -> Expr:
    """True when the row's ``file_path`` matches any configured test glob.

    ``check.builtin.test_only_production_code._is_test_path`` as a disjunction
    of :meth:`~pypeeker.dsl.Expr.matches` clauses — ``matches`` *is*
    ``fnmatchcase``, so this is the same predicate written in the grammar
    rather than smuggled into an opaque.
    """
    return allow_patterns(globs, row.file_path)


# ---------------------------------------------------------------------------
# the shared candidate prefix
# ---------------------------------------------------------------------------


def _candidate_clauses(
    *,
    kinds: tuple[SymbolKind, ...],
    visibilities: tuple[Visibility, ...],
    allow: tuple[str, ...],
    allow_decorators: tuple[str, ...],
) -> tuple[Expr, ...]:
    """The "eligible module-level symbol" prefix, in the frozen engine's written order.

    One clause per ``continue`` in the frozen rules' shared opening, same
    sequence:

    1. not in a ``__main__.py``;
    2. declared in a file the binder gave a MODULE symbol — the frozen rules'
       ``module_id is None -> continue``, ported as the :data:`MODULE_FILES`
       semi-join;
    3. one of the selected kinds;
    4. one of the selected visibilities;
    5. declared directly in the module body (not a method, not a nested def);
    6. not named ``main`` and not a dunder;
    7. not matched by the ``allow`` patterns — **omitted when no patterns are
       configured**, which is behaviour-identical (``_matches_any`` over an
       empty list is false) and keeps the built expression from advertising an
       option the rule was not given;
    8. not carrying an allowed decorator;
    9. not re-exported by a barrel — the semi-join, and the reason every rule
       built from this prefix reaches ``PROJECT``.

    The library-mode ``protected`` clause the frozen rules end with is
    deliberately absent; the module docstring argues why it is unreachable
    behind clause 8.

    Args:
        kinds: symbol kinds to consider.
        visibilities: visibilities to consider.
        allow: fnmatch patterns exempting a symbol by id or module path.
        allow_decorators: fnmatch patterns exempting a decorated symbol.

    Returns:
        The clauses, for a caller to conjoin with its own distinguishing test.
    """
    clauses: list[Expr] = [
        not_(row.file_path.matches("*__main__.py")),
        in_set(row.file_path, MODULE_FILES),
        row.kind.is_in(*kinds),
        row.visibility.is_in(*visibilities),
        row.is_module_level.is_true(),
        not_(any_of(row.name.eq("main"), _dunder_clause())),
    ]
    if allow:
        clauses.append(not_(_allow_clause(allow)))
    clauses.append(not_(_decorator_clause(allow_decorators)))
    clauses.append(not_(in_set(row.symbol_id, BARREL_EXPORTS)))
    return tuple(clauses)


def _dunder_clause() -> Expr:
    """``name.startswith("__") and name.endswith("__")``, in the grammar.

    ``matches("*__")`` is exactly ``endswith("__")`` because
    :meth:`~pypeeker.dsl.Expr.matches` is ``fnmatchcase`` and the pattern's
    only metacharacter is the leading ``*``. Written this way rather than as a
    new operator: the grammar already had the operator.
    """
    return all_of(row.name.startswith("__"), row.name.matches("*__"))


# ---------------------------------------------------------------------------
# the five rules
# ---------------------------------------------------------------------------


def unused_public_symbol(options: Mapping[str, Any]) -> Selection:
    """Module-level public functions and classes nothing in the project references.

    The candidate prefix plus one clause: the symbol's id is not in
    :data:`REFERENCED`, the set of canonical definition ids every reference in
    the corpus resolves to.

    Options:
        ``also-private``     — also report unreferenced ``_protected`` and
                               ``__private`` module-level symbols.
        ``allow-decorators`` — fnmatch patterns over decorator source text.
        ``visibility``       — the injected ``[tool.pypeeker.visibility]`` table.

    This rule has no ``allow`` option — the frozen one does not either — so the
    prefix is built with no patterns and carries no allow clause.
    """
    visibilities = (
        (Visibility.PUBLIC, Visibility.PROTECTED, Visibility.PRIVATE)
        if bool(options.get("also-private"))
        else (Visibility.PUBLIC,)
    )
    return symbols().where(
        all_of(
            *_candidate_clauses(
                kinds=_FUNCTION_OR_CLASS,
                visibilities=visibilities,
                allow=(),
                allow_decorators=_merged_allow_decorators(options),
            ),
            not_(in_set(row.symbol_id, REFERENCED)),
            DYNAMIC_ACCESS_WEAKENING,
        )
    )


def over_exposed_module_symbol(options: Mapping[str, Any]) -> Selection:
    """Public module-level symbols no *other* module references.

    The candidate prefix plus one clause: no module other than this row's own
    appears in :data:`~pypeeker.dsl.USAGE_ORIGINS` for it. A symbol with no
    references at all is still flagged — its observed scope is at most its own
    module — which is why the test is "no *other* origin" rather than "some
    origin here".

    ``column_of(USAGE_ORIGINS).any_other_than(row.module)`` is a comparison
    with an expression right-hand side, so both sides appear in the node's
    children and the reach derives through them.

    Options: ``kinds``, ``allow``, ``allow-decorators``, ``visibility``.
    """
    return symbols().where(
        all_of(
            *_candidate_clauses(
                kinds=_selected_kinds(options.get("kinds")),
                visibilities=(Visibility.PUBLIC,),
                allow=_as_str_list(options.get("allow")),
                allow_decorators=_merged_allow_decorators(options),
            ),
            not_(column_of(USAGE_ORIGINS).any_other_than(row.module)),
            DYNAMIC_ACCESS_WEAKENING,
        )
    )


def test_only_production_code(options: Mapping[str, Any]) -> Selection:
    """Public production symbols whose only references come from test files.

    The test-path exclusion comes **first**, ahead of the ``__main__.py``
    clause, because that is the frozen rule's order: a symbol defined in a test
    file is out of scope before anything else is asked about it.

    Then the candidate prefix, then the two reference-set clauses: not in the
    production reference set, and in the test one. Both conditions are
    required — a symbol with no references at all belongs to
    ``unused-public-symbol``, not here.

    The two sets partition every non-``DEFINITION`` reference in the corpus by
    the path it occurs at, and each resolves through
    ``follow("definition")``. That inversion is exact rather than
    approximate: the frozen rule's ``resolver.references_to_definition(id)`` is
    defined as ``[ref for ref in all references if resolve_reference(ref) ==
    resolve_definition(id)]``, which is this forward map read backwards. The
    one real difference — the frozen rule *also* discards a non-``DEFINITION``
    reference sitting at the symbol's own start position — is a declared
    divergence; see ``dsl-rewrite.md``'s ledger.

    Options: ``test-globs``, ``allow``, ``allow-decorators``, ``visibility``.
    """
    globs = _as_str_list(options.get("test-globs")) or DEFAULT_TEST_GLOBS
    return symbols().where(
        all_of(
            not_(_test_path_clause(globs)),
            *_candidate_clauses(
                kinds=_FUNCTION_OR_CLASS,
                visibilities=(Visibility.PUBLIC,),
                allow=_as_str_list(options.get("allow")),
                allow_decorators=_merged_allow_decorators(options),
            ),
            not_(in_set(row.symbol_id, _reference_set(globs, in_tests=False))),
            in_set(row.symbol_id, _reference_set(globs, in_tests=True)),
            DYNAMIC_ACCESS_WEAKENING,
        )
    )


def born_private(options: Mapping[str, Any]) -> Selection:
    """Newly public module-local symbols absent from the recorded symbol baseline.

    ``over-exposed-module-symbol``'s signal applied prospectively: the same
    candidate prefix and the same "no other module uses it" clause, with two
    extra clauses, both about ``.pypeeker/check-baseline.json``.

    The first clause is the **armed gate**, and it comes first because the
    frozen rule's equivalent is an early return that precedes every finding:

    .. code-block:: python

        if not has_symbol_baseline(path):
            write_symbol_baseline(path, set(current))
            return []

    An unseeded project produces no findings there, and produces none here —
    ``in_set(Const("symbols"), BASELINE_NAMESPACES)`` is false, and a false
    conjunct at the head of the ``all_of`` empties the rule. The two engines
    therefore agree on *findings* for every baseline state: unseeded (both
    silent), seeded-empty (both flag the whole module-local surface), seeded
    (both flag only what is unrecorded).

    They differ in one thing, and only one: **this rule never writes.** The
    frozen rule wrote the baseline as it returned, mid-run. Here the seed is a
    separate step at the application layer:
    :func:`pypeeker.app.check_run._seed_born_private` calls
    :func:`born_private_surface` — the same candidate prefix these clauses use,
    minus this gate and minus the ``RECORDED_PUBLIC_SYMBOLS`` negation — and
    writes the namespace, on an unseeded project and again behind
    ``--update-baseline``. So a real ``pypeeker check`` **does** arm the ratchet
    on a first run; what moved is the writer, not the behaviour, and
    ``dsl-rewrite.md``'s ledger records the divergence as closed at the flip.
    Keeping the write out of the rule is what lets
    :meth:`pypeeker.app.check_run.CheckRun.mutating_rules` narrow the
    ``check --fix`` fixpoint structurally instead of by name.

    That is also why the gate is expressed here rather than inherited: the rule
    must produce the right findings for every baseline state on its own,
    including the unseeded one it can no longer create.

    The second clause is the exemption proper — an id recorded in the
    ``"symbols"`` namespace is legacy and never relitigated.

    Options: ``kinds``, ``allow``, ``allow-decorators``, ``visibility``.
    """
    return symbols().where(
        all_of(
            in_set(Const(_SYMBOLS_KEY), BASELINE_NAMESPACES),
            *_candidate_clauses(
                kinds=_selected_kinds(options.get("kinds")),
                visibilities=(Visibility.PUBLIC,),
                allow=_as_str_list(options.get("allow")),
                allow_decorators=_merged_allow_decorators(options),
            ),
            not_(in_set(row.symbol_id, RECORDED_PUBLIC_SYMBOLS)),
            not_(column_of(USAGE_ORIGINS).any_other_than(row.module)),
            DYNAMIC_ACCESS_WEAKENING,
        )
    )


def born_private_surface(options: Mapping[str, Any]) -> Selection:
    """The symbol ids ``born-private`` seeds into the baseline when first armed.

    :func:`born_private` is the *rule*; this is the **surface** the ratchet is
    armed against — the set the frozen rule writes on an unseeded project:

    .. code-block:: python

        if not has_symbol_baseline(path):
            write_symbol_baseline(path, set(current))
            return []

    where ``current`` is every eligible module-level public symbol. That
    eligibility test is the same candidate prefix :func:`born_private`'s
    exemption uses — expressed here by calling the same
    :func:`_candidate_clauses` with the same options — so the surface and the
    ratchet cannot drift apart: anything the rule would later exempt as
    "recorded" is exactly what this seeds.

    It is the candidate prefix and **nothing else**. No
    :data:`BASELINE_NAMESPACES` gate (the seed is what runs when that gate is
    *shut*), no :data:`RECORDED_PUBLIC_SYMBOLS` negation (there is nothing
    recorded yet), no ``USAGE_ORIGINS`` clause and no
    :data:`DYNAMIC_ACCESS_WEAKENING` — the frozen seed is computed before the
    module-local test and before any confidence is attached.

    Projected as ``symbol_id``, raw rather than through
    :data:`~pypeeker.dsl.DEFINITION_ID`, for the reason the module docstring
    gives: ``_KIND_CHOICES`` restricts candidates to functions, classes and
    variables, on which ``resolve_definition`` is the identity, so these *are*
    the canonical ids the frozen engine writes.

    Options: ``kinds``, ``allow``, ``allow-decorators``, ``visibility`` — the
    same table :func:`born_private` reads, because a surface seeded under one
    configuration and relitigated under another is the drift this exists to
    prevent.
    """
    return (
        symbols()
        .where(
            all_of(
                *_candidate_clauses(
                    kinds=_selected_kinds(options.get("kinds")),
                    visibilities=(Visibility.PUBLIC,),
                    allow=_as_str_list(options.get("allow")),
                    allow_decorators=_merged_allow_decorators(options),
                )
            )
        )
        .project("symbol_id")
    )


def over_exposed_export(options: Mapping[str, Any]) -> Selection:
    """Barrel re-exports of in-package definitions no outside consumer uses.

    The one rule in the family whose rows are ``IMPORT`` symbols rather than
    definitions, and the one that needs project columns rather than only
    semi-joins: every clause after the sixth asks something about the
    *definition* the import resolves to, while staying on the import's row.

    Its second clause, ``in_set(row.file_path, MODULE_FILES)``, is the same
    module-id guard :func:`_candidate_clauses` carries — the frozen rules'
    ``module_id is None -> continue`` — applied here to the barrel's own file
    rather than to the definition's, because this rule's row is the barrel
    import, not the export.

    Clause order matters once, and load-bearingly. ``column_of(DEFINITION_KIND)
    .ne(None)`` is the locatability test, and it comes **before**
    ``not_(column_of(DEFINITION_KIND).eq(SymbolKind.IMPORT))`` — because an
    unlocatable definition yields ``UNMATCHED``, ``UNMATCHED`` compares false
    against everything, and ``not_`` of false is **true**. Only the preceding
    locatability clause makes the pair reproduce the frozen rule's
    ``if target is None or target.kind == IMPORT: continue``.

    ``allow`` is matched against the export's own id and against the canonical
    definition's, as the frozen rule does. The canonical side is a project
    column, so it is written as a disjunction of ``matches`` clauses over
    :data:`~pypeeker.dsl.DEFINITION_ID` and
    :data:`~pypeeker.dsl.DEFINITION_MODULE` rather than through the opaque —
    an opaque body sees the row, not a column. ``DEFINITION_MODULE`` is the
    module id of the file declaring the definition, which equals the frozen
    rule's ``module_of(canonical)`` by the symbol-id format
    (``module:Scope.Chain:local``).

    Options: ``allow``, ``visibility``. In library mode the exports of barrels
    under a public root are never flagged — see :func:`_protected_exports`.
    """
    allow = _as_str_list(options.get("allow"))
    clauses: list[Expr] = [
        row.file_path.matches("*__init__.py"),
        in_set(row.file_path, MODULE_FILES),
        row.kind.eq(SymbolKind.IMPORT),
        row.imported_from.is_true(),
        row.is_module_level.is_true(),
        row.visibility.eq(Visibility.PUBLIC),
        column_of(DEFINITION_KIND).ne(None),
        not_(column_of(DEFINITION_KIND).eq(SymbolKind.IMPORT)),
        column_of(DEFINITION_MODULE).is_within(row.module),
    ]
    if allow:
        clauses.append(not_(_allow_clause(allow)))
        clauses.append(
            not_(
                any_of(
                    *(column_of(DEFINITION_ID).matches(pattern) for pattern in allow),
                    *(column_of(DEFINITION_MODULE).matches(pattern) for pattern in allow),
                )
            )
        )
    protected = _protected_exports(options)
    if protected is not None:
        clauses.append(not_(in_set(column_of(DEFINITION_ID), protected)))
    clauses.append(not_(column_of(USAGE_ORIGINS).any_outside(row.module)))
    clauses.append(DYNAMIC_ACCESS_WEAKENING)
    return symbols().where(all_of(*clauses))


# ---------------------------------------------------------------------------
# under-exposed-access (phase 3d) — the family's one rule over references
# ---------------------------------------------------------------------------


def _definition_dunder_clause() -> Expr:
    """The frozen ``_is_dunder`` applied to the *definition's* name, in the grammar.

    The column-side twin of :func:`_dunder_clause`, and it must stay spelled as
    the same **pair** of clauses rather than collapsing to ``matches("__*__")``.
    The single glob needs four characters, and ``__`` and ``___`` reach this
    clause: ``adapters.python_adapter.get_visibility`` classifies a name as
    ``DUNDER`` only when ``len(name) > 4``, so the short ones are ``PRIVATE``
    and survive the visibility test above. The frozen ``_is_dunder`` is
    ``startswith("__") and endswith("__")`` with no length test, so it skips
    them; a four-character glob would not, and the port would fire where the
    frozen engine is silent.
    """
    return all_of(
        column_of(DEFINITION_NAME).startswith("__"),
        column_of(DEFINITION_NAME).matches("*__"),
    )


def _access_allow_clause(patterns: tuple[str, ...]) -> Expr:
    """The frozen ``_allowed(canonical, allow)``, over the definition's id.

    ``under-exposed-access`` matches its ``allow`` patterns against the
    *target* definition, not against the referencing row, so the columns are
    project columns rather than :func:`_allow_clause`'s row fields. Each
    pattern is tested against the canonical id and then against that id's
    module path, interleaved per pattern so the written order is the frozen
    ``any(... or ...)``'s.
    """
    return allow_patterns(
        patterns, column_of(DEFINITION_ID), column_of(DEFINITION_ID_MODULE)
    )


def _under_exposed_base(options: Mapping[str, Any]) -> Selection:
    """Every reach-in the frozen rule reports, before the test-path partition.

    One row per reference site — the frozen rule's inner loop is over
    ``index.references`` with no ``DEFINITION``-kind exclusion, so every
    reference is a candidate. Its ``continue`` statements, in written order:

    1. the referencing file has a ``MODULE`` symbol (``origin is None ->
       continue``, the :data:`MODULE_FILES` semi-join, so ``row.module`` is a
       real module id for every surviving row and can be quoted);
    2. the reference resolves to something the corpus declares
       (``target is None -> continue``) — the locatability test, spelled as
       ``DEFINITION_KIND.ne(None)`` for the reason
       :func:`over_exposed_export` documents at length, and placed **before**
       the visibility test because ``UNMATCHED`` compares false against
       everything;
    3. that definition is ``_protected`` or ``__private``;
    4. its name is not a dunder;
    5. the definition's module is not the referencing module;
    6. the ``allow`` patterns do not exempt it.

    The three quoted values are then attached as derived fields, so both
    message templates stay ``str.format`` over visible fields.

    No :class:`~pypeeker.dsl.Weaken` node: the frozen rule is not a caller of
    ``check.rules._dynamic_access_confidence`` — see
    :data:`DYNAMIC_ACCESS_WEAKENED_RULES`, which names this rule as the
    non-member it is.
    """
    return (
        references()
        .where(
            all_of(
                in_set(row.file_path, MODULE_FILES),
                column_of(DEFINITION_KIND).ne(None),
                column_of(DEFINITION_VISIBILITY).is_in(
                    Visibility.PROTECTED, Visibility.PRIVATE
                ),
                not_(_definition_dunder_clause()),
                not_(column_of(DEFINITION_ID_MODULE).eq(row.module)),
                not_(_access_allow_clause(_as_str_list(options.get("allow")))),
            )
        )
        .with_field("target_visibility", column_of(DEFINITION_VISIBILITY))
        .with_field("target_name", column_of(DEFINITION_NAME))
        .with_field("target_module", column_of(DEFINITION_ID_MODULE))
    )


def _access_test_globs(options: Mapping[str, Any]) -> tuple[str, ...]:
    """The rule's ``test-globs`` option, or its own eight-pattern default."""
    return _as_str_list(options.get("test-globs")) or ACCESS_TEST_GLOBS


def under_exposed_access_from_tests(options: Mapping[str, Any]) -> Selection:
    """Reach-ins whose referencing file is classified as test code.

    Half of a complementary partition of :func:`_under_exposed_base` on
    :func:`_test_path_clause`; :func:`under_exposed_access_outside` is the
    other half. Two parts rather than one template with an interpolated
    ``{detail}`` because the frozen rule writes two literal message lines and
    picks between them — the ``naming-conventions`` precedent
    (:class:`~pypeeker.dsl.MultiPartRule` argues it), and fork #9's reason:
    half a message computed in Python is half a message the derivation tree
    cannot describe.

    Options: ``allow``, ``test-globs``.
    """
    return _under_exposed_base(options).where(_test_path_clause(_access_test_globs(options)))


def under_exposed_access_outside(options: Mapping[str, Any]) -> Selection:
    """Reach-ins from production code — the complement of the part above.

    Options: ``allow``, ``test-globs``.
    """
    return _under_exposed_base(options).where(
        not_(_test_path_clause(_access_test_globs(options)))
    )


# ---------------------------------------------------------------------------
# the parameterised sets
# ---------------------------------------------------------------------------


def _reference_set(globs: tuple[str, ...], *, in_tests: bool) -> ProjectedSet:
    """Definition ids referenced from test paths, or from everything else.

    The globs are folded into the set's name so ``--why`` names the partition
    it actually used; identity is structural regardless (see
    :class:`~pypeeker.dsl.ProjectedSet`), so two rules configured with the same
    globs share one scan and two configured differently never collide.

    ``ReferenceKind.DEFINITION`` rows are excluded on both sides: a definition
    is not a use.
    """
    side = "test" if in_tests else "production"
    path_matches = _test_path_clause(globs)
    return projected_set(
        f"{side}-references:{','.join(globs)}",
        references()
        .where(
            all_of(
                not_(row.kind.eq(ReferenceKind.DEFINITION)),
                path_matches if in_tests else not_(path_matches),
            )
        )
        .follow("definition")
        .project("symbol_id"),
    )


def _protected_exports(options: Mapping[str, Any]) -> ProjectedSet | None:
    """Barrel exports the library contract protects, or ``None`` in app mode.

    The port of ``check.rules._public_root_protected``, and the one place in
    this file where that clause survives (the module docstring says why it does
    not elsewhere). Three cases, matching
    ``VisibilityConfig.effective_public_roots``:

    * **app mode** — nothing is protected; ``None``, and the caller writes no
      clause at all rather than a clause that can never fire.
    * **library mode, explicit ``public-roots``** — the barrels whose package
      is one of those roots or nested beneath it.
    * **library mode, no roots** — the default is *every top-level package*, so
      every barrel qualifies and the protected set is exactly
      :data:`BARREL_EXPORTS`. Reusing that constant is not a shortcut: it is
      the same set, and sharing it means sharing its one scan.
    """
    if not _is_library(options):
        return None
    roots = _as_str_list(_visibility_table(options).get("public-roots"))
    if not roots:
        return BARREL_EXPORTS
    return projected_set(
        f"public-root-barrel-exports:{','.join(roots)}",
        symbols()
        .where(
            all_of(
                row.file_path.matches("*__init__.py"),
                row.kind.eq(SymbolKind.IMPORT),
                any_of(*(row.module.is_within(root) for root in roots)),
            )
        )
        .follow("definition")
        .project("symbol_id"),
    )


def _load_baseline_namespaces(corpus: Corpus) -> frozenset[str]:
    """Top-level namespace keys present in ``.pypeeker/check-baseline.json``.

    Read through :mod:`pypeeker.storage.baseline`, the one owner of the
    baseline file. The set :data:`BASELINE_NAMESPACES` is built from;
    membership of ``"symbols"`` in it is exactly that module's
    ``has_symbol_baseline``, down to the case its docstring singles out — a
    seeded-empty ``"symbols": []`` is *present*, and reads as "already seeded",
    not as "seed me again".
    """
    return baseline_namespaces(baseline_path(corpus.store.project_root))


def _load_recorded_symbols(corpus: Corpus) -> frozenset[str]:
    """Symbol ids recorded in the baseline's ``"symbols"`` namespace.

    :func:`pypeeker.storage.load_symbol_baseline`: a missing file or an absent
    namespace is an empty baseline. Telling those two apart from a
    seeded-empty one is :data:`BASELINE_NAMESPACES`'s job.
    """
    return frozenset(load_symbol_baseline(baseline_path(corpus.store.project_root)))


RECORDED_PUBLIC_SYMBOLS = corpus_set(
    "recorded-public-symbols",
    reads=("baseline:symbols",),
    load=_load_recorded_symbols,
)
"""``born-private``'s baseline: ids already public when the ratchet was seeded.

A :class:`~pypeeker.dsl.CorpusSet` rather than a
:class:`~pypeeker.dsl.ProjectedSet` because it is not a fact about the code at
all — it is a file on disk — so it declares what it reads instead of deriving
it, exactly as an opaque predicate must.
"""

BASELINE_NAMESPACES = corpus_set(
    "baseline-namespaces",
    reads=("baseline:namespaces",),
    load=_load_baseline_namespaces,
)
""":data:`RECORDED_PUBLIC_SYMBOLS`'s sibling: which namespaces the file declares.

Separate from the recorded ids because it answers a different question —
*is the ratchet armed?* rather than *what did it record?* — and because the two
cannot be collapsed: an armed ratchet that recorded nothing and an unarmed one
both load as the empty id set, and ``born-private`` must treat them as
opposites. Keyed on namespace names, so ``in_set(Const("symbols"), …)`` is the
membership test that reproduces ``check.baseline.has_symbol_baseline``.
"""
