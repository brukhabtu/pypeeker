"""Application service: the ``check`` command's run.

Everything ``check`` does between parsing its flags and printing — read
``[tool.pypeeker]``, refuse an import-boundaries table that would enforce
nothing, import the configured plugin modules, resolve the rule names, seed the
born-private ratchet, run the rules, sort — lives here so the CLI keeps only
options, output and exit codes.

Three things differ from the frozen ``check`` engine this replaced, each
deliberate and each carried in ``dsl-rewrite.md``'s divergence ledger:

- **An unknown configured rule name refuses** (``UnknownExpressionError``,
  naming every known id) where the frozen ``CheckEngine.run`` silently skipped
  it — a typo'd rule id used to read as a clean run.
- **The run record carries the config, not an engine.** ``check --fix``'s
  fixpoint needs to re-run the rules against a simulated store, which means a
  fresh :class:`~pypeeker.dsl.Corpus` over that store — so :class:`CheckRun`
  hands on ``src``/``rules``/``options`` and builds the corpus at the point of
  use.
- **Findings are sorted explicitly.** The frozen ``Violation`` was
  ``order=True`` and the engine sorted on it; ``Finding`` is not orderable and
  the DSL returns rows in per-rule index order, so :func:`finding_order` is the
  one owner of report order.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pypeeker.app.boundary_config import validate_boundary_config
from pypeeker.dsl import (
    Corpus,
    Finding,
    MultiPartRule,
    PortedRule,
    born_private_surface,
    dsl_rule,
    install_expressions,
    read_config,
)
from pypeeker.storage import (
    IndexStore,
    baseline_path,
    clear_symbol_baseline,
    delta,
    has_symbol_baseline,
    load_baseline,
    write_baseline,
    write_symbol_baseline,
)

# The rule id is spelled here rather than imported from the rule table: the
# born-private *seed* is an app-layer act (it writes a `.pypeeker/` file), and
# the id is what `[tool.pypeeker].rules` contains, not an object.
# ``tests/test_app_check_run.py`` asserts the spelling resolves.
_BORN_PRIVATE = "born-private"

__all__ = [
    "BaselineDelta",
    "BaselineUpdate",
    "CheckConfigError",
    "CheckRun",
    "check_baseline_delta",
    "run_check",
    "update_check_baseline",
]


class CheckConfigError(Exception):
    """Raised when check configuration (e.g. a plugin module) can't be loaded.

    Carries the message text of the frozen ``check.engine.CheckConfigError``
    verbatim: the CLI reports it to users, so it is contract, not a detail.
    """


def _declares_mutation(rule: PortedRule) -> bool:
    """True when ``rule`` can produce a repair for at least one of its rows.

    The predicate the ``check --fix`` fixpoint narrows its per-iteration re-run
    by, replacing the frozen ``SIMULATION_UNSAFE_RULES`` constant: a rule that
    declares no mutation yields no ``Remediation``, so running it inside the
    loop can only cost time.

    :class:`~pypeeker.dsl.MultiPartRule` has no ``mutation`` of its own — each
    of its parts carries one — so a bare ``rule.mutation`` would raise
    ``AttributeError`` on ``import-boundaries``, ``star-imports`` and
    ``docstring-drift``.
    """
    if isinstance(rule, MultiPartRule):
        return any(part.mutation is not None for part in rule.parts)
    return rule.mutation is not None


def finding_order(finding: Finding) -> tuple[str, int, str, str]:
    """The one order ``check`` reports in: ``(path, line, rule, message)``.

    ``Finding`` is deliberately not ``order=True`` (the frozen ``Violation``
    is), so this order is imposed by the service that produces the findings
    rather than carried by the value — and it is the same order
    :func:`pypeeker.storage.delta` requires of its input, which is why the two
    obligations are discharged by one sort.

    Public, though absent from ``__all__``: :mod:`pypeeker.app.fix_run` sorts
    the repairs it collects by the same key (a repair's ``declined`` entry is
    emitted in input order and never re-sorted, so the two must agree), and one
    owner of the order is the point. Not barrel-exported — no consumer outside
    ``app`` has any business imposing ``check``'s report order.
    """
    return (finding.path, finding.line, finding.rule, finding.message)


def _run_rules(
    rules: Sequence[tuple[str, PortedRule]],
    options: Mapping[str, dict],
    corpus: Corpus,
) -> list[Finding]:
    """Every rule's findings over one corpus, sorted."""
    found: list[Finding] = []
    for name, rule in rules:
        found.extend(rule.findings(options.get(name, {}), corpus))
    found.sort(key=finding_order)
    return found


def load_plugins(plugins: Sequence[str], root: Path) -> None:
    """Import configured plugin modules so they register their rules.

    Public, though absent from ``__all__``, on the same terms as
    :func:`finding_order`: :mod:`pypeeker.app.batch_intents` must import a
    ``fix`` entry's plugin modules before :func:`~pypeeker.dsl.dsl_rule`
    resolves the name, or a batch naming a plugin rule regresses from working
    to refusing. Two in-package consumers, one owner of the ``sys.path``
    dance. Not barrel-exported — importing a project's plugins is ``app``'s
    business.

    Lifted verbatim from ``check.engine.CheckEngine._load_plugins``: the
    project root is placed on ``sys.path`` so in-repo rule modules (e.g. a
    top-level ``lint_rules.py``) are importable, not just installed packages,
    and it is removed again afterwards so a check run leaves no trace on the
    importer.

    Raises:
        CheckConfigError: a configured module could not be imported.
    """
    if not plugins:
        return
    root_str = str(root)
    added = root_str not in sys.path
    if added:
        sys.path.insert(0, root_str)
    try:
        for module in plugins:
            try:
                importlib.import_module(module)
            except ImportError as exc:
                raise CheckConfigError(
                    f"could not import check plugin '{module}': {exc}"
                ) from exc
    finally:
        if added:
            sys.path.remove(root_str)


@dataclass(frozen=True)
class CheckRun:
    """One run of the configured rules: what was found, and what would re-find it.

    ``findings`` is the FULL set, never filtered by confidence — display
    filtering is the CLI's, baselines always see all — and is sorted by
    ``(path, line, rule, message)``.

    The other three fields are the run's *configuration*, kept so a caller can
    re-run without re-reading ``pyproject.toml`` and risking a different rule
    set. That is what the frozen service handed on a ``CheckEngine`` for; the
    new engine has no such object, because a run is a corpus plus a rule list
    and the corpus is bound to a store the caller may want to vary (the fix
    fixpoint runs against a simulation overlay).
    """

    findings: list[Finding]
    src: tuple[str, ...]
    rules: tuple[tuple[str, PortedRule], ...]
    options: Mapping[str, dict]

    def rerun(self, store: IndexStore) -> list[Finding]:
        """Re-run every configured rule against ``store``, sorted as this run was.

        A **fresh** :class:`~pypeeker.dsl.Corpus`: a corpus memoises every
        sweep for its lifetime, so re-using one would answer from the indexes
        it first saw — which is exactly wrong for the caller that needs this
        (``check --fix`` computing residual findings after applying repairs).
        """
        return _run_rules(self.rules, self.options, Corpus(store, self.src))

    def mutating_rules(self) -> tuple[tuple[str, PortedRule], ...]:
        """The configured rules that declare a mutation, in configured order.

        The ``check --fix`` fixpoint re-runs only these per iteration: the
        others can contribute no repair, so including them changes nothing but
        the clock. Output-neutral by construction, and the reason the frozen
        ``SIMULATION_UNSAFE_RULES`` constant has no successor.
        """
        return tuple((name, rule) for name, rule in self.rules if _declares_mutation(rule))


def _seed_born_private(
    root: Path,
    store: IndexStore,
    src: tuple[str, ...],
    options: Mapping[str, dict],
    *,
    reseed: bool,
) -> None:
    """Arm the born-private ratchet when it is enabled and unarmed.

    The frozen rule seeds itself mid-run (it writes the ``"symbols"``
    namespace and returns ``[]`` on an unseeded project); the ported rule does
    not write at all, so the write moves here — the layer that already owns
    ``.pypeeker/`` artifacts — driven by
    :func:`pypeeker.dsl.born_private_surface`, the same candidate prefix the
    rule's own exemption uses.

    ``reseed`` is ``--update-baseline``'s half of the contract: clear first, so
    the seeding below records TODAY's public surface as accepted.

    The surface is evaluated over a **dedicated** corpus, thrown away before
    the run corpus is built. A corpus memoises its sweeps, and two of them
    (``BASELINE_NAMESPACES``, ``RECORDED_PUBLIC_SYMBOLS``) read the very file
    this writes; sharing one corpus across the write would make the rule's
    answer depend on which sweep happened to materialise first.
    """
    path = baseline_path(root)
    if reseed:
        clear_symbol_baseline(path)
    if has_symbol_baseline(path):
        return
    surface = born_private_surface(options.get(_BORN_PRIVATE, {}))
    seed_corpus = Corpus(store, src)
    write_symbol_baseline(
        path, {match.fields["symbol_id"] for match in surface.rows(seed_corpus)}
    )


def run_check(
    store: IndexStore, root: Path, *, reseed_symbol_baseline: bool = False
) -> CheckRun:
    """Load the project's check config, resolve the rules and run every one.

    Raises:
        BoundaryConfigError: the import-boundaries table names a nested unit.
            Such a table would run clean while enforcing nothing, so the run is
            refused rather than reported as a pass the project cannot trust.
        CheckConfigError: a configured plugin module could not be imported.
        UnknownExpressionError: ``[tool.pypeeker].rules`` names a rule no
            builtin and no plugin provides. The frozen engine skipped such a
            name in silence, which made a typo read as a clean run; the
            message names every id that *is* reachable. Resolution happens
            **after** plugin import, so a plugin's own rule ids count.

    ``reseed_symbol_baseline`` is ``--update-baseline``'s half of the
    born-private contract: when that rule is enabled, the accepted-public
    symbol namespace is cleared and re-seeded from the current public surface.
    """
    install_expressions()
    src, rule_names, plugins, options = read_config(root)
    validate_boundary_config(options.get("import-boundaries", {}))
    load_plugins(plugins, store.project_root)
    rules = tuple((name, dsl_rule(name)) for name in rule_names)
    if _BORN_PRIVATE in rule_names:
        _seed_born_private(
            root, store, src, options, reseed=reseed_symbol_baseline
        )
    findings = _run_rules(rules, options, Corpus(store, src))
    return CheckRun(findings=findings, src=src, rules=rules, options=options)


@dataclass(frozen=True)
class BaselineUpdate:
    """What ``--update-baseline`` recorded: how many, and where."""

    recorded: int
    path: Path


@dataclass(frozen=True)
class BaselineDelta:
    """``--baseline``'s comparison: the recorded total, and what moved."""

    baselined: int
    new: list[Finding]
    fixed: list[str]


def update_check_baseline(root: Path, findings: list[Finding]) -> BaselineUpdate:
    """Record ``findings`` as the project's new baseline.

    Takes the full set, never a confidence-filtered one: a baseline must not
    churn with ``--strict``.
    """
    path = baseline_path(root)
    counts = write_baseline(path, findings)
    return BaselineUpdate(recorded=sum(counts.values()), path=path)


def check_baseline_delta(root: Path, findings: list[Finding]) -> BaselineDelta:
    """Compare ``findings`` against the stored baseline.

    The delta is over the full set — identities must match what was recorded —
    and a missing baseline file counts as empty, so every finding is new.

    The input is sorted by :func:`finding_order` **here**, so
    :func:`pypeeker.storage.delta`'s precondition cannot be violated by a
    caller. ``delta`` does not sort its own input (a reported row is not
    required to be orderable) and it attributes a surplus over the baselined
    count to the LAST occurrences *in the order given*, so an unsorted list
    would silently name different findings as new — a precondition discharged
    by convention is a precondition waiting to be broken. The sort is
    idempotent for :func:`run_check`'s already-ordered output, which is
    the only intended producer.
    """
    baseline = load_baseline(baseline_path(root))
    new, fixed = delta(sorted(findings, key=finding_order), baseline)
    return BaselineDelta(baselined=sum(baseline.values()), new=new, fixed=fixed)
