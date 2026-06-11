"""Finding-to-demotion handoff for the batch privatize planner (TASK-97).

Three check rules nominate symbols for demotion (``name -> _name``):
``over-exposed-module-symbol``, ``unused-public-symbol`` and
``test-only-production-code``. Their shared mechanized fix is the batch
demotion planner (:func:`pypeeker.refactor.privatize.plan_privatize`), but
``refactor`` may not import ``check`` (see
:mod:`pypeeker.refactor.intents`), so the planner takes plain
``(symbol_id, confidence_str)`` pairs and the *caller* extracts them from
violations with :func:`demote_entry` — the same handoff idiom as
:func:`pypeeker.check.builtin.naming_conventions.rename_pair`.

Each of the three rules embeds the full symbol id in its message (the
parsers here must mirror those formats); the confidence string is the
violation's :class:`~pypeeker.models.capabilities.Confidence` value, which
the planner's pre-filter uses to exclude heuristic-confidence findings from
auto-fix by default.

This module is not a builtin rule module (nothing registers here), so it may
be imported freely by callers (the ``privatize`` CLI command, tests) without
the engine-import cycle concerns the builtin modules document.
"""

from __future__ import annotations

import re

from pypeeker.check.builtin.test_only_production_code import (
    TEST_ONLY_PRODUCTION_CODE,
)
from pypeeker.check.builtin.visibility import OVER_EXPOSED_MODULE_SYMBOL
from pypeeker.check.models import Violation
from pypeeker.check.rules import UNUSED_PUBLIC_SYMBOL

DEMOTION_RULES: tuple[str, ...] = (
    OVER_EXPOSED_MODULE_SYMBOL,
    UNUSED_PUBLIC_SYMBOL,
    TEST_ONLY_PRODUCTION_CODE,
)
"""The check rules whose findings feed the batch demotion planner."""

_MESSAGE_RES: dict[str, re.Pattern[str]] = {
    # Parsers for the finding messages; each must mirror the format in the
    # owning rule's body (the rename_pair precedent).
    OVER_EXPOSED_MODULE_SYMBOL: re.compile(
        r"^public '(?P<symbol_id>[^']+)' is only used within its module"
        r" — make it _\S+$"
    ),
    UNUSED_PUBLIC_SYMBOL: re.compile(
        r"^\S+ \S+ '(?P<symbol_id>[^']+)' has no references in the project$"
    ),
    TEST_ONLY_PRODUCTION_CODE: re.compile(
        r"^'(?P<symbol_id>[^']+)' is referenced only from tests"
        r" \(\d+ test references?\)$"
    ),
}


def demote_entry(violation: Violation) -> tuple[str, str] | None:
    """The ``(symbol_id, confidence_str)`` pair carried by a finding, or None.

    This is the handoff to the batch demotion planner
    (:func:`pypeeker.refactor.privatize.plan_privatize`): the returned pair
    is exactly one :data:`~pypeeker.refactor.privatize.CandidateEntry`.
    Returns ``None`` for violations of rules outside :data:`DEMOTION_RULES`
    and for messages that don't match the owning rule's format (a custom
    rule reusing the rule name, or a format drift this helper's tests would
    catch).
    """
    pattern = _MESSAGE_RES.get(violation.rule)
    if pattern is None:
        return None
    match = pattern.match(violation.message)
    if match is None:
        return None
    return match.group("symbol_id"), violation.confidence.value
