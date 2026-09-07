"""Validation of ``[tool.pypeeker.import-boundaries]`` against the engine that runs it.

This guard was written against the engine the rewrite replaced, whose
``import-boundaries`` rule read a unit as exactly one package segment beneath
``root`` (``check.rules._package_under``). Under that reading a **dotted** unit
name — ``domain.orders`` — named nothing a module could ever resolve to, and
left alone it was not an error but a *silence*: the key matched no unit, no
import was ever charged against it, and under ``strict`` the bare parent was
still reported undeclared. A project on a ``src/<pkg>/`` layout wrote the
nested boundary it wanted, got a clean run, and concluded the boundary was
enforced. It was not.

Refusing the config was the whole point: an unenforceable declaration must fail
loudly rather than pass quietly, because the quiet pass is indistinguishable
from a real one. It lives in ``app`` rather than inside the rule engine because
it is a statement *about* that engine's reach — a refusal to run at all —
rather than a rule that computes findings.

**Status after the flip.** The guard is live, not vestigial:
:func:`pypeeker.app.check_run.run_check` calls it on every run and documents
the refusal in its own ``Raises``. But the engine it now guards is the DSL one,
and that rule resolves a module to the **longest declared unit prefix**
(:func:`pypeeker.dsl.sweeps._unit_under`), so ``domain.orders`` *is* resolvable
there — TASK-169's nested-unit support. The refusal is therefore now strictly
conservative: it rejects a table the running rule could honor, rather than one
it would silently ignore. The flip changed no behaviour here deliberately —
narrowing the guard to what ``_unit_under`` actually cannot resolve (and
un-blocking nested units through ``pypeeker check``) is follow-up work, not a
documentation edit. See TASK-169 and ``dsl-rewrite.md``. Note that
``barrel-only`` still reads packages flatly, through
:func:`pypeeker.dsl.sweeps._package_under`; that is its own ``root`` option and
its whole notion of a package, not a limitation shared with this table.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["BoundaryConfigError", "dotted_boundary_units", "validate_boundary_config"]


class BoundaryConfigError(ValueError):
    """An ``import-boundaries`` table this guard refuses to run a check against."""


def dotted_boundary_units(options: Mapping[str, Any]) -> tuple[str, ...]:
    """Every dotted unit name the table declares, in any of its three positions.

    Sorted and de-duplicated. A name is collected from an ``allow`` key (the
    importer side), from a dependency inside an ``allow`` list (the imported
    side), and from ``unconstrained`` — all three are positions where a unit
    name is expected, so all three can carry a dotted one.

    ``root`` is deliberately not inspected: it is a dotted *prefix* by design
    (``a.b`` is a legitimate root) and units are named relative to it.
    """
    names: set[str] = set()

    allow = options.get("allow")
    if isinstance(allow, Mapping):
        for importer, deps in allow.items():
            if isinstance(importer, str):
                names.add(importer)
            if isinstance(deps, (list, tuple)):
                names.update(d for d in deps if isinstance(d, str))

    unconstrained = options.get("unconstrained")
    if isinstance(unconstrained, (list, tuple)):
        names.update(u for u in unconstrained if isinstance(u, str))

    return tuple(sorted(n for n in names if "." in n))


def validate_boundary_config(options: Mapping[str, Any]) -> None:
    """Raise when the table declares a dotted unit name.

    Conservative by construction — see the module docstring: the refusal is
    calibrated to the flat, one-segment reading of a unit, which the running
    rule has since widened.

    Args:
        options: The ``[tool.pypeeker.import-boundaries]`` table, as
            :func:`pypeeker.app.check_run.run_check` reads it out of the
            project's ``[tool.pypeeker]`` section.

    Raises:
        BoundaryConfigError: One or more dotted unit names are declared. The
            message names every one of them, so a project fixes the whole table
            in a single pass rather than one failure at a time.
    """
    dotted = dotted_boundary_units(options)
    if not dotted:
        return
    listed = ", ".join(repr(name) for name in dotted)
    raise BoundaryConfigError(
        f"import-boundaries: nested unit name(s) {listed} cannot be enforced. "
        f"A boundary unit is one package segment beneath 'root', so a dotted "
        f"name matches no package and would be silently ignored — reporting "
        f"nothing while looking like a passing check. Either set 'root' deeper "
        f"so the layer you want to police becomes the first segment beneath it, "
        f"or declare the single-segment parent instead. Nested units are "
        f"supported by the new engine (see TASK-169)."
    )
