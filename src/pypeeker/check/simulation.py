"""Which rules must not be re-run against a *simulated* project state.

``check --fix --fix-until-clean`` (:mod:`pypeeker.app.check_fixes`) re-runs
the configured rules once per iteration against an
:class:`~pypeeker.storage.overlay.OverlayIndexStore` layered over the real
store, so each iteration sees the repairs the previous one made without
anything reaching disk. Most rules are pure functions of the index and are
therefore indifferent to that; the ones named here are not, and the reason
is **write safety**, not fidelity.

An overlay's ``project_root`` is the *real* project root — it delegates to
the base store, and stays real however deeply overlays nest. A rule that
persists state keyed to that root therefore writes into the user's tree
during a run whose entire promise is that the tree is untouched until the
single apply at the end. ``born-private`` is exactly that rule: it seeds its
accepted-public-symbol baseline on first enablement with
``write_symbol_baseline(baseline_path(context.store.project_root), ...)``
(see :mod:`pypeeker.check.builtin.born_private`), which under an overlay
lands in the real ``.pypeeker/``. Under the pre-overlay temp-dir mirror the
same write was a harmless self-seed inside the copy; under the overlay it is
a real side effect, so the exclusion is a hard guard rather than a nicety.

The criterion for adding a rule here: **its execution writes (or otherwise
persists state) through ``context.store.project_root``.** A rule that merely
*reads* project-root state is fine — the overlay's simulated bytes are the
whole point. Only the loop's per-iteration runs are narrowed; the residual
run after the apply uses the original engine with the original config, so an
excluded rule still reports normally on the real, final tree.
"""

from __future__ import annotations

SIMULATION_UNSAFE_RULES: frozenset[str] = frozenset({"born-private"})
"""Rules excluded from a fixpoint iteration's re-run (see the module docstring).

Names, not imports: :mod:`pypeeker.check.builtin` modules self-register their
rules as an import side effect, and this constant is re-exported from the
``check`` package barrel — importing the rule module here would make
``import pypeeker.check`` register every builtin rule, which
:meth:`pypeeker.check.engine.CheckEngine.run` deliberately defers. The names
are pinned to their defining constants by test.
"""
