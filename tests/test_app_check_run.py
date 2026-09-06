"""``app/check_run.py``, the run service behind ``pypeeker check``.

Where the frozen engine had a counterpart, its answer on the same fixture was
captured before it was deleted and is recorded here as a literal — so these
tests still say what the old engine said, not merely what the new one says.

Three behaviours here are deliberately *not* what the frozen service did, and
each is asserted as a difference rather than papered over:

- an unknown configured rule name refuses instead of being skipped in silence;
- the born-private seed is written by this layer, from a DSL selection, rather
  than by the rule mid-run;
- findings are sorted by an explicit key rather than by ``Violation``'s
  ``order=True``.
"""

from __future__ import annotations

import sys

import pytest

from pypeeker.app import (
    BoundaryConfigError,
    CheckConfigError,
    check_baseline_delta,
    run_check,
    update_check_baseline,
)
from pypeeker.dsl import rules as dsl_rules_module
from pypeeker.dsl.errors import UnknownExpressionError
from pypeeker.storage import (
    baseline_path,
    clear_symbol_baseline,
    has_symbol_baseline,
    load_symbol_baseline,
)


@pytest.fixture(autouse=True)
def isolated_dsl_registry():
    """Keep a plugin's ``register_dsl_rule`` call from leaking into other tests."""
    saved = dict(dsl_rules_module._REGISTERED)
    saved_modules = set(sys.modules)
    saved_path = list(sys.path)
    try:
        yield
    finally:
        dsl_rules_module._REGISTERED.clear()
        dsl_rules_module._REGISTERED.update(saved)
        for name in set(sys.modules) - saved_modules:
            sys.modules.pop(name, None)
        sys.path[:] = saved_path


@pytest.fixture
def configured_project(indexed_project):
    """``indexed_project`` plus a ``pyproject.toml`` both run services read."""

    def _setup(files: dict[str, str], config: str):
        root, store = indexed_project(files)
        (root / "pyproject.toml").write_text(config)
        return root, store

    return _setup


def _config(rules: list[str], *, extra: str = "", src: str = '["src"]') -> str:
    listed = ", ".join(f'"{rule}"' for rule in rules)
    return f"[tool.pypeeker]\nsrc = {src}\nrules = [{listed}]\n{extra}"


# ── (a) the two engines agree on what the project's rules find ──────────────

_TWO_FILES = {
    "src/alpha.py": (
        "import os\n"
        "\n"
        "\n"
        "def alpha():\n"
        "    return 1\n"
    ),
    "src/beta.py": (
        '"""Beta."""\n'
        "\n"
        "\n"
        "def beta():\n"
        "    return [1, 2]\n"
    ),
}


_FROZEN_TWO_FILE_REPORT = [
    "src/alpha.py:1: [unused-imports] import 'os' is unused in this module",
    "src/alpha.py:4: [require-docstrings] public function 'alpha' has no docstring",
    "src/beta.py:4: [require-docstrings] public function 'beta' has no docstring",
]
"""What the frozen service reported on ``_TWO_FILES``, captured before it died."""


def test_the_service_finds_what_the_frozen_one_found(configured_project):
    """Same project, same configured rules, same reported lines — byte for byte.

    Compared as ``str(...)`` because ``Finding.__str__`` is byte-identical to
    ``Violation.__str__``; that is the whole point of keeping it so, and it
    covers path, line, rule, message and the confidence marker at once.
    """
    root, store = configured_project(
        _TWO_FILES, _config(["require-docstrings", "unused-imports", "prefer-tuple"])
    )

    run = run_check(store, root)

    assert [str(f) for f in run.findings] == _FROZEN_TWO_FILE_REPORT


def test_the_run_record_carries_the_config_needed_to_re_run(configured_project):
    """``rerun`` re-evaluates against a store, and agrees with the original run."""
    root, store = configured_project(_TWO_FILES, _config(["require-docstrings"]))

    run = run_check(store, root)

    assert run.src == ("src",)
    assert [name for name, _ in run.rules] == ["require-docstrings"]
    assert [str(f) for f in run.rerun(store)] == [str(f) for f in run.findings]


def test_mutating_rules_is_the_subset_that_can_repair(configured_project):
    """The fixpoint's narrowing: ``born-private`` declares no mutation, so it is out.

    Also the guard against a bare ``rule.mutation``: ``star-imports`` is a
    :class:`~pypeeker.dsl.MultiPartRule`, which carries the mutation on its
    parts and would raise ``AttributeError`` on the attribute.
    """
    root, store = configured_project(
        _TWO_FILES,
        _config(["born-private", "require-docstrings", "unused-imports", "star-imports"]),
    )

    run = run_check(store, root)

    assert [name for name, _ in run.mutating_rules()] == [
        "unused-imports",
        "star-imports",
    ]


# ── the engine mechanics, ported from tests/test_check_engine.py ────────────
#
# Three scenarios that used to be asserted against ``CheckEngine.run()``
# directly. They are about the run service's plumbing rather than any one
# rule's verdict, so they belong here now that the service IS the engine.


def test_no_configured_rules_reports_nothing(configured_project):
    """An empty ``rules`` list is a legitimate, silent configuration."""
    root, store = configured_project(_TWO_FILES, _config([]))

    run = run_check(store, root)

    assert run.findings == []
    assert run.rules == ()


def test_the_src_roots_filter_what_the_rules_can_see(configured_project):
    """A file outside every configured source root is not a candidate at all.

    The filter lives in the :class:`~pypeeker.dsl.Corpus` the service builds,
    so this is the only place it is observable end to end.
    """
    root, store = configured_project(
        {
            "src/m.py": "def foo():\n    return 1\n",
            "tests/m.py": "def bar():\n    return 1\n",
        },
        _config(["require-docstrings"]),
    )

    run = run_check(store, root)

    messages = [f.message for f in run.findings]
    assert any("foo" in message for message in messages)
    assert not any("bar" in message for message in messages)


def test_each_rule_is_run_with_its_own_option_table(configured_project):
    """``[tool.pypeeker.<rule>]`` reaches the rule, not just the config reader.

    ``require-docstrings`` ignores protected names by default, so a finding
    about ``_h`` can only come from the option table being threaded through.
    """
    root, store = configured_project(
        {"src/m.py": "def _h():\n    return 1\n"},
        _config(
            ["require-docstrings"],
            extra='[tool.pypeeker.require-docstrings]\nvisibility = ["protected"]\n',
        ),
    )

    run = run_check(store, root)

    assert any("_h" in f.message for f in run.findings)


# ── (b) the final order ─────────────────────────────────────────────────────


def test_findings_are_sorted_by_path_line_rule_message(configured_project):
    """Two rules firing on one line tie-break by rule id, then by message.

    ``Finding`` is not ``order=True``, so this order exists only because the
    service imposes it — which is also what discharges
    :func:`pypeeker.storage.delta`'s unsorted-input hazard.
    """
    files = {
        "src/b.py": "def badName():\n    return 1\n",
        "src/a.py": '"""A."""\n\n\ndef aBadName():\n    return 1\n',
    }
    root, store = configured_project(
        files, _config(["naming-conventions", "require-docstrings"])
    )

    run = run_check(store, root)

    keys = [(f.path, f.line, f.rule, f.message) for f in run.findings]
    assert keys == sorted(keys)
    assert ("src/b.py", 1, "naming-conventions") == keys[-2][:3]
    assert ("src/b.py", 1, "require-docstrings") == keys[-1][:3]
    # And it is the order the frozen service produced from ``Violation``'s
    # ``order=True``, captured before it died.
    assert [str(f) for f in run.findings] == [
        "src/a.py:4: [naming-conventions] function 'src.a:aBadName' does not match "
        "the snake_case naming convention — suggested name: 'a_bad_name'",
        "src/a.py:4: [require-docstrings] public function 'aBadName' has no docstring",
        "src/b.py:1: [naming-conventions] function 'src.b:badName' does not match "
        "the snake_case naming convention — suggested name: 'bad_name'",
        "src/b.py:1: [require-docstrings] public function 'badName' has no docstring",
    ]


# ── (c) an unknown configured rule name ─────────────────────────────────────


def test_an_unknown_rule_name_refuses_where_the_frozen_engine_stayed_silent(
    configured_project,
):
    """The deliberate divergence, ledgered at the flip.

    The frozen engine dropped a name it could not resolve and reported a clean
    run — so a typo'd rule id disabled a rule invisibly. This service raises,
    naming every id that *is* reachable.
    """
    root, store = configured_project(_TWO_FILES, _config(["require-docstrigs"]))

    with pytest.raises(UnknownExpressionError) as excinfo:
        run_check(store, root)
    message = str(excinfo.value)
    assert "require-docstrigs" in message
    assert "require-docstrings" in message


# ── (d) plugins ─────────────────────────────────────────────────────────────

_PLUGIN_SOURCE = (
    '"""A consumer project\'s own rule."""\n'
    "\n"
    "from pypeeker.dsl import DslRule, register_dsl_rule, symbols\n"
    "\n"
    "register_dsl_rule(\n"
    "    DslRule(\n"
    '        rule_id="acme-house-style",\n'
    "        build=lambda options: symbols(),\n"
    '        message="acme saw {name}",\n'
    "    )\n"
    ")\n"
)


def test_a_plugin_module_registers_a_rule_the_run_then_resolves(configured_project):
    """The public extension point, end to end: import first, resolve second.

    Resolution has to happen *after* the import or a plugin's own rule id is
    an unknown name. The module lives at the project root, not on
    ``sys.path``, which is what the temporary insert is for.
    """
    root, store = configured_project(
        _TWO_FILES, _config(["acme-house-style"], extra='plugins = ["lint_rules"]\n')
    )
    (root / "lint_rules.py").write_text(_PLUGIN_SOURCE)
    before = list(sys.path)

    run = run_check(store, root)

    assert {f.rule for f in run.findings} == {"acme-house-style"}
    assert any(f.message == "acme saw alpha" for f in run.findings)
    assert sys.path == before, "the project root must not be left on sys.path"


def test_a_plugin_import_failure_refuses_with_the_frozen_message(configured_project):
    """The refusal text is the frozen one, verbatim — it reaches users."""
    root, store = configured_project(
        _TWO_FILES,
        _config(["require-docstrings"], extra='plugins = ["nope_missing_xyz"]\n'),
    )

    with pytest.raises(CheckConfigError) as excinfo:
        run_check(store, root)
    assert str(excinfo.value).startswith(
        "could not import check plugin 'nope_missing_xyz': "
    )


# ── (e) the born-private seed ───────────────────────────────────────────────

_SURFACE_FILES = {
    "src/pkg/__init__.py": '"""Barrel."""\n',
    "src/pkg/core.py": (
        '"""Core."""\n'
        "\n"
        "\n"
        "def exported() -> int:\n"
        '    """E."""\n'
        "    return 1\n"
        "\n"
        "\n"
        "def _private() -> int:\n"
        '    """P."""\n'
        "    return 2\n"
    ),
}


def test_an_unarmed_ratchet_seeds_and_reports_nothing(configured_project):
    """First run on an unseeded project: write the surface, report no findings."""
    root, store = configured_project(_SURFACE_FILES, _config(["born-private"]))
    assert not has_symbol_baseline(baseline_path(root))

    run = run_check(store, root)

    assert run.findings == []
    seeded = load_symbol_baseline(baseline_path(root))
    assert seeded, "an empty seed would make every later assertion vacuous"
    assert any(symbol_id.endswith(":exported") for symbol_id in seeded)
    assert not any(symbol_id.endswith(":_private") for symbol_id in seeded)


def test_the_seed_equals_the_frozen_rules_seed(configured_project):
    """What this layer writes is what the frozen rule wrote, on the same fixture.

    The frozen rule seeded as a side effect of running; its answer on
    ``_SURFACE_FILES`` was captured before it was deleted and is the literal
    below. ``_private`` is excluded because it is already private, and
    ``pkg:__init__`` because a barrel is not a candidate.
    """
    root, store = configured_project(_SURFACE_FILES, _config(["born-private"]))
    frozen_seed = {"src.pkg.core:exported"}
    clear_symbol_baseline(baseline_path(root))

    run_check(store, root)

    assert load_symbol_baseline(baseline_path(root)) == frozen_seed


def test_an_armed_ratchet_is_not_re_seeded_and_reports_against_itself(
    configured_project, indexed_project
):
    """Second run: the namespace is left alone, and a newly public symbol fires."""
    root, store = configured_project(_SURFACE_FILES, _config(["born-private"]))
    run_check(store, root)
    seeded = load_symbol_baseline(baseline_path(root))

    grown = dict(_SURFACE_FILES)
    grown["src/pkg/core.py"] += (
        "\n\ndef newly_public() -> int:\n"
        '    """N."""\n'
        "    return 3\n"
    )
    _, store = indexed_project(grown)

    run = run_check(store, root)

    assert load_symbol_baseline(baseline_path(root)) == seeded
    assert [f.rule for f in run.findings] == ["born-private"]
    assert "newly_public" in run.findings[0].message


def test_reseeding_accepts_todays_surface(configured_project, indexed_project):
    """``--update-baseline``'s half: clear, re-seed, and the finding goes away."""
    root, store = configured_project(_SURFACE_FILES, _config(["born-private"]))
    run_check(store, root)

    grown = dict(_SURFACE_FILES)
    grown["src/pkg/core.py"] += (
        "\n\ndef newly_public() -> int:\n"
        '    """N."""\n'
        "    return 3\n"
    )
    _, store = indexed_project(grown)

    reseeded = run_check(store, root, reseed_symbol_baseline=True)

    assert reseeded.findings == []
    assert any(
        symbol_id.endswith(":newly_public")
        for symbol_id in load_symbol_baseline(baseline_path(root))
    )


def test_the_seed_only_runs_when_the_rule_is_enabled(configured_project):
    """A project that does not ask for the ratchet gets no ``symbols`` namespace."""
    root, store = configured_project(_SURFACE_FILES, _config(["require-docstrings"]))

    run_check(store, root)

    assert not has_symbol_baseline(baseline_path(root))


# ── (f) the import-boundaries guard is unchanged ────────────────────────────


def test_a_nested_boundary_unit_is_still_refused(configured_project):
    """The guard runs before any rule does, as it did on the frozen service.

    A dotted unit name matches no package: the table would run clean while
    enforcing nothing, so the run is refused rather than reported as a pass.
    """
    config = _config(
        ["import-boundaries"],
        extra=(
            "\n[tool.pypeeker.import-boundaries]\n"
            'root = "src"\n'
            "\n[tool.pypeeker.import-boundaries.allow]\n"
            '"domain.orders" = []\n'
        ),
    )
    root, store = configured_project(_TWO_FILES, config)

    with pytest.raises(BoundaryConfigError, match="domain.orders"):
        run_check(store, root)


# ── the baseline flows over the new findings ────────────────────────────────


def test_recording_then_comparing_reports_nothing_new(configured_project):
    """``--update-baseline`` then ``--baseline``: the round trip is empty."""
    root, store = configured_project(
        _TWO_FILES, _config(["require-docstrings", "unused-imports"])
    )
    run = run_check(store, root)

    recorded = update_check_baseline(root, run.findings)
    compared = check_baseline_delta(root, run.findings)

    assert recorded.recorded == len(run.findings)
    assert recorded.path == baseline_path(root)
    assert compared.baselined == len(run.findings)
    assert compared.new == []
    assert compared.fixed == []


def test_a_finding_absent_from_the_baseline_is_new(configured_project, indexed_project):
    """And one that disappears is reported fixed, by its stored identity."""
    root, store = configured_project(_TWO_FILES, _config(["require-docstrings"]))
    update_check_baseline(root, run_check(store, root).findings)

    grown = dict(_TWO_FILES)
    grown["src/beta.py"] += "\n\ndef undocumented():\n    return 3\n"
    _, store = indexed_project(grown)

    compared = check_baseline_delta(root, run_check(store, root).findings)

    assert len(compared.new) == 1
    assert "undocumented" in compared.new[0].message
    assert compared.fixed == []
