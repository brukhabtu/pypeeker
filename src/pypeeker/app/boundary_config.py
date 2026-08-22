"""Validation of ``[tool.pypeeker.import-boundaries]`` against the engine that runs it.

The rule's unit is one package segment beneath ``root``
(``check.rules._package_under``), so a **dotted** unit name — ``domain.orders``
— names nothing the frozen engine can ever resolve a module to. Left alone it
is not an error there but a *silence*: the key matches no unit, no import is
ever charged against it, and under ``strict`` the bare parent is still reported
undeclared. A project on a ``src/<pkg>/`` layout writes the nested boundary it
wants, gets a clean run, and concludes the boundary is enforced. It is not.

Refusing the config is the whole point: an unenforceable declaration must fail
loudly rather than pass quietly, because the quiet pass is indistinguishable
from a real one. This lives outside ``check/`` — a frozen oracle path for the
DSL rewrite — because it is a statement *about* that engine's reach rather than
a change to what it computes.

The new ``dsl`` engine resolves a module to the longest declared unit prefix and
does support nested units (:func:`pypeeker.dsl.sweeps._unit_under`), so this
guard is scoped to the old engine and is deleted at the phase-5 flip along with
the path it guards. See TASK-169 and ``dsl-rewrite.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["BoundaryConfigError", "dotted_boundary_units", "validate_boundary_config"]


class BoundaryConfigError(ValueError):
    """An ``import-boundaries`` table the running engine cannot honor as written."""


def dotted_boundary_units(options: Mapping[str, Any]) -> tuple[str, ...]:
    """Every dotted unit name the table declares, in any of its three positions.

    Sorted and de-duplicated. A name is collected from an ``allow`` key (the
    importer side), from a dependency inside an ``allow`` list (the imported
    side), and from ``unconstrained`` — all three are positions where a unit
    name is expected, so all three can carry one the flat engine cannot match.

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
    """Raise when the table names a unit the flat engine cannot resolve to.

    Args:
        options: The ``[tool.pypeeker.import-boundaries]`` table, as the check
            engine hands it to the rule.

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
