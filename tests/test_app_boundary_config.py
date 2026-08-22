"""The guard that refuses an ``import-boundaries`` table the flat engine can't honor.

A dotted unit name against the frozen engine is not an error but a silence: it
matches no package, charges no import, and leaves the run looking clean. These
tests pin that the guard turns that silence into a refusal, and — just as
importantly — that it stays out of the way of every configuration written for
the flat engine.
"""

import pytest

from pypeeker.app import (
    BoundaryConfigError,
    dotted_boundary_units,
    validate_boundary_config,
)

FLAT = {
    "root": "pypeeker",
    "strict": True,
    "unconstrained": ["cli"],
    "allow": {"models": [], "binder": ["adapters", "models", "paths"]},
}


# ---------------------------------------------------------------------------
# what counts as a nested unit name
# ---------------------------------------------------------------------------


def test_a_flat_table_names_nothing_nested():
    assert dotted_boundary_units(FLAT) == ()


def test_a_dotted_importer_key_is_collected():
    assert dotted_boundary_units({"allow": {"domain.orders": []}}) == ("domain.orders",)


def test_a_dotted_dependency_value_is_collected():
    """The imported side counts: a nested dependency is equally unmatchable."""
    assert dotted_boundary_units({"allow": {"api": ["domain.billing"]}}) == (
        "domain.billing",
    )


def test_a_dotted_unconstrained_entry_is_collected():
    assert dotted_boundary_units({"unconstrained": ["ui.widgets"]}) == ("ui.widgets",)


def test_a_dotted_root_is_not_a_nested_unit():
    """``root`` is a dotted prefix by design; units are named relative to it."""
    assert dotted_boundary_units({"root": "a.b.c", "allow": {"api": []}}) == ()


def test_every_position_is_reported_together_sorted_and_deduplicated():
    """One pass over the table names every offender, so one edit fixes it."""
    options = {
        "allow": {"ui.widgets": ["ui.app"], "api": ["ui.app"]},
        "unconstrained": ["ui.widgets"],
    }
    assert dotted_boundary_units(options) == ("ui.app", "ui.widgets")


# ---------------------------------------------------------------------------
# the refusal
# ---------------------------------------------------------------------------


def test_a_flat_table_passes_untouched():
    validate_boundary_config(FLAT)


def test_an_empty_table_passes():
    validate_boundary_config({})


def test_a_nested_table_is_refused_and_the_message_names_every_offender():
    options = {"allow": {"domain.orders": ["domain.billing"]}}
    with pytest.raises(BoundaryConfigError) as excinfo:
        validate_boundary_config(options)
    message = str(excinfo.value)
    assert "'domain.billing'" in message
    assert "'domain.orders'" in message


def test_the_message_says_why_silence_was_the_alternative():
    """The refusal has to explain what it prevented, or it reads as pedantry."""
    with pytest.raises(BoundaryConfigError) as excinfo:
        validate_boundary_config({"allow": {"a.b": []}})
    assert "silently ignored" in str(excinfo.value)


def test_malformed_values_do_not_crash_the_guard():
    """Config is user input; a wrong-typed table must not turn into a traceback."""
    options = {"allow": {"api": "not-a-list", 3: ["x"]}, "unconstrained": None}
    assert dotted_boundary_units(options) == ()
