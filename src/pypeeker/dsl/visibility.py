"""The visibility / reference-counting rule family, as expressions.

Phase 3b of ``dsl-rewrite.md``. Five rules that all ask one question — *who
actually uses this?* — and answer it by counting references across the whole
corpus rather than by reading one file:

* ``unused-public-symbol`` — nothing anywhere references it.
* ``over-exposed-module-symbol`` — only its own module references it.
* ``over-exposed-export`` — only its own package references the definition a
  barrel re-exports.
* ``born-private`` — the same module-local test, applied prospectively against
  a recorded baseline.
* ``test-only-production-code`` — only test files reference it.

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

Four of the five open with the same eight clauses — :func:`_candidate_clauses`
— because the frozen rules open with the same eight ``continue`` statements.
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

Configuration is re-read, not imported
--------------------------------------

``dsl`` may not import ``project``, so :func:`_visibility_table` and friends
re-implement the slice of ``pypeeker.project.parse_visibility_config`` these
rules observe. The same sanctioned duplication
:func:`pypeeker.dsl.differential._read_config` already makes, and for the same
reason: the new engine must never execute old-engine code, or the oracle would
grade a thing against itself.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pypeeker.dsl.columns import DEFINITION_ID, DEFINITION_KIND, DEFINITION_MODULE, USAGE_ORIGINS
from pypeeker.dsl.columns import column_of
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.expr import Const, Expr, all_of, any_of, not_, opaque, row, weakened_when
from pypeeker.dsl.joins import ProjectedSet, corpus_set, in_set, projected_set
from pypeeker.dsl.selection import Selection, references, symbols
from pypeeker.models import (
    Confidence,
    ReferenceKind,
    SymbolKind,
    Visibility,
    builtin_id,
    module_of,
)

UNUSED_PUBLIC_SYMBOL = "unused-public-symbol"
OVER_EXPOSED_MODULE_SYMBOL = "over-exposed-module-symbol"
OVER_EXPOSED_EXPORT = "over-exposed-export"
BORN_PRIVATE = "born-private"
TEST_ONLY_PRODUCTION_CODE = "test-only-production-code"

DYNAMIC_ACCESS_WEAKENED_RULES: frozenset[str] = frozenset({
    UNUSED_PUBLIC_SYMBOL,
    OVER_EXPOSED_MODULE_SYMBOL,
    OVER_EXPOSED_EXPORT,
    BORN_PRIVATE,
    TEST_ONLY_PRODUCTION_CODE,
})
"""Exactly the rules the dynamic-access weakening applies to. Enumerated, not derived.

The frozen engine calls ``check.rules._dynamic_access_confidence`` from four
modules at five call sites — ``check/rules.py:587`` (unused-public-symbol),
``check/builtin/visibility.py:277`` (over-exposed-module-symbol) and ``:373``
(**over-exposed-export**, a second rule in the same module and the one an
inventory by module would miss), ``check/builtin/born_private.py:209``, and
``check/builtin/test_only_production_code.py:179``. No other caller exists;
``visibility.py``'s third rule ``under-exposed-access`` does not weaken, and
neither does any other consumer of ``resolve_definition``.

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

_DYNAMIC_ACCESS_BUILTIN_IDS: tuple[str, ...] = tuple(
    builtin_id(name) for name in ("getattr", "globals", "vars", "locals")
)
"""Resolved builtin reference ids that signal dynamic symbol access."""

_BASELINE_FILE = "check-baseline.json"
_SYMBOLS_KEY = "symbols"
_STORAGE_DIR = ".pypeeker"
_LEGACY_STORAGE_DIR = ".semantic-tool"


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

    ``check.rules._as_str_list``, returning a tuple because nothing here
    mutates the result and a tuple is hashable.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    return tuple(str(value) for value in raw)


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
    """The raw ``[tool.pypeeker.visibility]`` table ``check.config`` injects, or empty.

    ``check.config.load_config`` copies the project-wide visibility section into
    *every* enabled rule's options under the reserved ``visibility`` key, and
    ``pypeeker.project.coerce_visibility`` parses it. That parse is tolerant —
    a missing table, an unknown ``mode``, non-list values all fall back to
    defaults — and so is this, for the same reason and with the same result.
    ``project`` is not in ``dsl``'s layering allow-list, so the slice is
    re-read here (see the module docstring).

    Only the raw mapping shape is accepted. ``coerce_visibility`` also takes an
    already-parsed ``VisibilityConfig``, but that type lives in ``project``,
    which this package may not import — a parsed config cannot legally cross
    into ``dsl``, so a non-mapping non-None value here is a wiring mistake and
    refuses loudly rather than silently degrading to app-mode defaults.
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
# opaque clauses: fnmatch over a configured pattern list
# ---------------------------------------------------------------------------


def _matches_any(symbol_id: str, patterns: Iterable[str]) -> bool:
    """True when a pattern fnmatches ``symbol_id`` or its module path.

    ``check.rules._matches_any`` and ``check.builtin.visibility._allowed`` are
    the same function under two names; this is it. ``module_of`` is the
    ``split(":", 1)[0]`` both of them perform.
    """
    module_path = module_of(symbol_id)
    return any(
        fnmatch.fnmatchcase(symbol_id, pattern) or fnmatch.fnmatchcase(module_path, pattern)
        for pattern in patterns
    )


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
    """The ``allow`` option as a declared-reads opaque over ``symbol_id``.

    Opaque rather than expressed, because fnmatch against a configured pattern
    list is genuinely a body the grammar cannot see into. Fork #9's price is
    the ``reads=`` declaration, and it is honest here: the body looks at one
    field and nothing else. Nothing about which rows the rule *selects* hides
    in it — the pattern list is pure configuration.
    """

    @opaque("allow-pattern", reads=("symbol_id",))
    def _allowed(record: Any) -> bool:
        return _matches_any(record.symbol_id, patterns)

    return _allowed


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
    return any_of(*(row.file_path.matches(glob) for glob in globs))


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
    2. one of the selected kinds;
    3. one of the selected visibilities;
    4. declared directly in the module body (not a method, not a nested def);
    5. not named ``main`` and not a dunder;
    6. not matched by the ``allow`` patterns — **omitted when no patterns are
       configured**, which is behaviour-identical (``_matches_any`` over an
       empty list is false) and keeps the built expression from advertising an
       option the rule was not given;
    7. not carrying an allowed decorator;
    8. not re-exported by a barrel — the semi-join, and the reason every rule
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

    They differ in one thing, and only one: **this port does not self-seed.**
    The frozen rule writes the baseline as it returns; seeding is a write, and
    the read half of the DSL has no mutation terminals (phase 2's rule, phase
    4's business). So a first run leaves the ratchet unarmed here where the old
    engine would arm it. That is a divergence in effect, not in output, it is
    declared in ``dsl-rewrite.md``'s ledger, and it is why the gate is
    expressed rather than inherited: without it the port's agreement with the
    old engine on this repository would depend on the old engine having run
    first and written the file the new one reads.

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


def over_exposed_export(options: Mapping[str, Any]) -> Selection:
    """Barrel re-exports of in-package definitions no outside consumer uses.

    The one rule in the family whose rows are ``IMPORT`` symbols rather than
    definitions, and the one that needs project columns rather than only
    semi-joins: every clause after the fifth asks something about the
    *definition* the import resolves to, while staying on the import's row.

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


def _storage_root(project_root: Path) -> Path:
    """``.pypeeker``, or a pre-rename ``.semantic-tool`` when only that exists.

    A local re-derivation of ``pypeeker.storage.index_store.resolve_storage_root``,
    which ``storage``'s ``__init__`` barrel does not re-export — and
    ``barrel-only`` forbids reaching past a barrel into another package's
    submodule. Eight lines of duplication against a rule violation is the
    right trade, and the same one ``dsl/differential.py`` already makes for
    config loading.
    """
    new = project_root / _STORAGE_DIR
    if new.exists():
        return new
    legacy = project_root / _LEGACY_STORAGE_DIR
    if legacy.exists():
        return legacy
    return new


def _baseline_document(corpus: Corpus) -> Mapping[str, Any]:
    """The parsed baseline file, or an empty mapping when there is nothing to parse.

    A missing file and a file whose top level is not an object both read as "no
    namespaces", which is what ``check.baseline``'s two readers do with their
    ``path.exists()`` and ``isinstance(data, dict)`` guards. Malformed JSON is
    *not* smoothed over here either: ``json.loads`` raises on both sides.
    """
    path = _storage_root(corpus.store.project_root) / _BASELINE_FILE
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_baseline_namespaces(corpus: Corpus) -> frozenset[str]:
    """Top-level namespace keys present in ``.pypeeker/check-baseline.json``.

    The set :data:`BASELINE_NAMESPACES` is built from; membership of
    ``"symbols"`` in it is exactly ``check.baseline.has_symbol_baseline``, down
    to the case that function's docstring singles out — a seeded-empty
    ``"symbols": []`` is *present*, and reads as "already seeded", not as "seed
    me again".
    """
    return frozenset(str(key) for key in _baseline_document(corpus))


def _load_recorded_symbols(corpus: Corpus) -> frozenset[str]:
    """Symbol ids recorded in the baseline's ``"symbols"`` namespace.

    ``check.baseline.load_symbol_baseline``: a missing file or an absent
    namespace is an empty baseline. Telling those two apart from a
    seeded-empty one is :data:`BASELINE_NAMESPACES`'s job, exactly as it is
    ``has_symbol_baseline``'s in the frozen engine.
    """
    raw = _baseline_document(corpus).get(_SYMBOLS_KEY, [])
    return frozenset(str(symbol_id) for symbol_id in raw)


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
