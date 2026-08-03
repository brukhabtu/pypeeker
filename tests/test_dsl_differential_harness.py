"""Tests for the DSL rewrite's differential oracle (scripts/differential-check.py).

The harness is what phase 1 of dsl-rewrite.md's plan calls "the oracle" — the
thing every later phase is graded against. These tests are what stop it from
passing vacuously: they pin the old-engine text parser, the new-engine JSON
parser, the ledger-anchored divergence bookkeeping, the generated-TOML
writer, and — end to end — that an empty manifest is trivially green while a
fabricated divergence on a claimed rule fails loudly.

The harness lives in ``scripts/`` (a dev tool, deliberately outside
``src/``), so it is loaded by path rather than imported as a package —
same idiom as ``tests/test_envl_replay_harness.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parent.parent / "scripts" / "differential-check.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("differential_check", _HARNESS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["differential_check"] = module
    spec.loader.exec_module(module)
    return module


dc = _load_harness()


# --------------------------------------------------------------------------
# Old-engine (text) parsing
# --------------------------------------------------------------------------


def test_parse_old_line_declared_has_no_tier_marker():
    finding = dc.parse_old_line("src/pypeeker/x.py:12: [prefer-tuple] use a tuple here")
    assert finding == dc.Finding(
        rule="prefer-tuple",
        path="src/pypeeker/x.py",
        line=12,
        confidence="declared",
        message="use a tuple here",
    )


def test_parse_old_line_strips_trailing_tier_marker():
    finding = dc.parse_old_line(
        "src/pypeeker/x.py:12: [over-exposed-module-symbol] foo is too public [heuristic]"
    )
    assert finding.confidence == "heuristic"
    assert finding.message == "foo is too public"


def test_parse_old_line_rejects_unparseable_input():
    with pytest.raises(dc.HarnessError, match="not a real check line at all"):
        dc.parse_old_line("not a real check line at all")


def test_parse_old_output_rejects_finding_for_unclaimed_rule():
    text = "src/x.py:1: [some-rule] message here"
    with pytest.raises(dc.HarnessError, match="unclaimed rule 'some-rule'"):
        dc.parse_old_output(text, claimed=("other-rule",), scrub=[])


def test_parse_old_output_scrubs_workdir_and_sorts():
    text = (
        "/tmp/work/b.py:2: [r1] second\n"
        "/tmp/work/a.py:1: [r1] first\n"
    )
    findings = dc.parse_old_output(text, claimed=("r1",), scrub=["/tmp/work"])
    # Scrub replaces the work-dir prefix with the "<root>" placeholder rather
    # than leaving a raw absolute path in the normalized finding.
    assert [f.path for f in findings] == ["<root>/a.py", "<root>/b.py"]


# --------------------------------------------------------------------------
# New-engine (JSON) parsing
# --------------------------------------------------------------------------


def _new_payload(findings):
    return json.dumps({"schema": 1, "findings": findings})


def test_parse_new_output_rejects_wrong_schema():
    payload = json.dumps({"schema": 2, "findings": []})
    with pytest.raises(dc.HarnessError, match="unsupported schema"):
        dc.parse_new_output(payload, claimed=(), scrub=[])


def test_parse_new_output_rejects_missing_key():
    payload = _new_payload(
        [{"rule": "r1", "path": "a.py", "line": 1, "confidence": "declared"}]
    )
    with pytest.raises(dc.HarnessError, match="wrong keys"):
        dc.parse_new_output(payload, claimed=("r1",), scrub=[])


def test_parse_new_output_rejects_unknown_confidence():
    payload = _new_payload(
        [{"rule": "r1", "path": "a.py", "line": 1, "message": "m", "confidence": "certain"}]
    )
    with pytest.raises(dc.HarnessError, match="unknown confidence"):
        dc.parse_new_output(payload, claimed=("r1",), scrub=[])


def test_parse_new_output_rejects_zero_line():
    payload = _new_payload(
        [{"rule": "r1", "path": "a.py", "line": 0, "message": "m", "confidence": "declared"}]
    )
    with pytest.raises(dc.HarnessError, match="invalid line"):
        dc.parse_new_output(payload, claimed=("r1",), scrub=[])


def test_parse_new_output_scrubs_and_sorts():
    payload = _new_payload(
        [
            {"rule": "r1", "path": "/tmp/work/b.py", "line": 2, "message": "b", "confidence": "declared"},
            {"rule": "r1", "path": "/tmp/work/a.py", "line": 1, "message": "a", "confidence": "declared"},
        ]
    )
    findings = dc.parse_new_output(payload, claimed=("r1",), scrub=["/tmp/work"])
    assert [f.path for f in findings] == ["<root>/a.py", "<root>/b.py"]


# --------------------------------------------------------------------------
# Compare core
# --------------------------------------------------------------------------


def test_compare_identical_findings_yield_no_missing_or_extra():
    f = dc.Finding("r1", "a.py", 1, "declared", "msg")
    report = dc.compare("t", ("r1",), [f], [f], ())
    (rule,) = report.rules
    assert rule.missing == () and rule.extra == ()
    assert not dc._is_failing(report)


def test_compare_isolates_missing_and_extra_per_rule():
    old = [dc.Finding("r1", "a.py", 1, "declared", "only old"), dc.Finding("r2", "c.py", 3, "declared", "same")]
    new = [dc.Finding("r1", "b.py", 2, "declared", "only new"), dc.Finding("r2", "c.py", 3, "declared", "same")]
    report = dc.compare("t", ("r1", "r2"), old, new, ())
    by_rule = {r.rule: r for r in report.rules}
    assert [f.message for f in by_rule["r1"].missing] == ["only old"]
    assert [f.message for f in by_rule["r1"].extra] == ["only new"]
    assert by_rule["r2"].missing == () and by_rule["r2"].extra == ()


def test_compare_default_key_includes_message():
    old = [dc.Finding("r1", "a.py", 1, "declared", "old wording")]
    new = [dc.Finding("r1", "a.py", 1, "declared", "new wording")]
    report = dc.compare("t", ("r1",), old, new, ())
    (rule,) = report.rules
    assert len(rule.missing) == 1 and len(rule.extra) == 1


def test_compare_message_divergence_absorbs_wording_change_and_is_applied():
    old = [dc.Finding("r1", "a.py", 1, "declared", "old wording")]
    new = [dc.Finding("r1", "a.py", 1, "declared", "new wording")]
    div = dc.Divergence(rule="r1", kind="message", ledger="message wording is freely improvable")
    report = dc.compare("t", ("r1",), old, new, (div,))
    (rule,) = report.rules
    assert rule.missing == () and rule.extra == ()
    assert rule.applied == ("message:r1",)
    assert rule.unused == ()
    assert not dc._is_failing(report)


def test_compare_finding_divergence_absorbs_declared_finding_and_is_applied():
    old = [dc.Finding("r1", "a.py", 1, "declared", "expected"), dc.Finding("r1", "b.py", 2, "declared", "shared")]
    new = [dc.Finding("r1", "b.py", 2, "declared", "shared")]
    div = dc.Divergence(
        rule="r1", kind="finding", ledger="anchor text", side="old", path="a.py", line=1
    )
    report = dc.compare("t", ("r1",), old, new, (div,))
    (rule,) = report.rules
    assert rule.missing == () and rule.extra == ()
    assert rule.applied == ("finding:old:a.py:1",)
    assert not report.errors
    assert not dc._is_failing(report)


def test_compare_finding_divergence_that_matches_nothing_is_a_stale_hard_failure():
    both = [dc.Finding("r1", "b.py", 2, "declared", "shared")]
    div = dc.Divergence(
        rule="r1", kind="finding", ledger="anchor text", side="old", path="a.py", line=1
    )
    report = dc.compare("t", ("r1",), both, both, (div,))
    (rule,) = report.rules
    assert rule.unused == ("finding:old:a.py:1",)
    assert report.errors
    assert "stale divergence declaration" in report.errors[0]
    assert dc._is_failing(report)


def test_compare_unused_message_divergence_is_reported_but_does_not_fail():
    f = dc.Finding("r1", "a.py", 1, "declared", "same wording")
    div = dc.Divergence(rule="r1", kind="message", ledger="anchor text")
    report = dc.compare("t", ("r1",), [f], [f], (div,))
    (rule,) = report.rules
    assert rule.unused == ("message:r1",)
    assert rule.applied == ()
    assert not report.errors
    assert not dc._is_failing(report)


# --------------------------------------------------------------------------
# TOML writer
# --------------------------------------------------------------------------


def test_render_config_toml_round_trips_through_tomllib():
    import tomllib

    section = {
        "src": ["src"],
        "rules": ["a", "b"],
        "report-unused-allowances": True,
        "import-boundaries": {
            "root": "pypeeker",
            "strict": True,
            "unconstrained": [],
            "allow": {"models": [], "adapters": ["models"]},
        },
    }
    rendered = dc.render_config_toml(section)
    parsed = tomllib.loads(rendered)
    assert parsed == {"tool": {"pypeeker": section}}


def test_render_config_toml_rejects_dict_nested_in_list():
    with pytest.raises(dc.HarnessError, match="dict nested inside a TOML list"):
        dc.render_config_toml({"bad": [{"x": 1}]})


# --------------------------------------------------------------------------
# Manifest parsing
# --------------------------------------------------------------------------


def _base_manifest(**overrides) -> dict:
    data = {
        "version": 1,
        "claimed": [],
        "target": [{"name": "self", "path": "."}],
    }
    data.update(overrides)
    return data


def test_parse_manifest_rejects_wrong_version():
    with pytest.raises(dc.HarnessError, match="unsupported manifest version"):
        dc.parse_manifest(_base_manifest(version=2), source="<test>")


def test_parse_manifest_rejects_unknown_top_level_key():
    with pytest.raises(dc.HarnessError, match="unknown top-level key"):
        dc.parse_manifest(_base_manifest(bogus=1), source="<test>")


def test_parse_manifest_rejects_duplicate_claimed_rule():
    with pytest.raises(dc.HarnessError, match="duplicate rule name"):
        dc.parse_manifest(_base_manifest(claimed=["r1", "r1"]), source="<test>")


def test_parse_manifest_rejects_divergence_for_unclaimed_rule():
    data = _base_manifest(
        claimed=["r1"],
        divergence=[{"rule": "r2", "kind": "message", "ledger": "anchor text"}],
    )
    with pytest.raises(dc.HarnessError, match="not in 'claimed'"):
        dc.parse_manifest(data, source="<test>")


def test_parse_manifest_rejects_finding_divergence_without_side():
    data = _base_manifest(
        claimed=["r1"],
        divergence=[{"rule": "r1", "kind": "finding", "ledger": "anchor text", "path": "a.py", "line": 1}],
    )
    with pytest.raises(dc.HarnessError, match="needs 'side'"):
        dc.parse_manifest(data, source="<test>")


def test_parse_manifest_rejects_short_ledger_anchor():
    data = _base_manifest(
        claimed=["r1"],
        divergence=[{"rule": "r1", "kind": "message", "ledger": "short"}],
    )
    with pytest.raises(dc.HarnessError, match="ledger.*anchor"):
        dc.parse_manifest(data, source="<test>")


def test_parse_manifest_rejects_message_divergence_carrying_a_message():
    # A kind="message" divergence suppresses message comparison for the whole
    # rule. Accepting a 'message' key would read as a one-wording sanction while
    # silently applying rule-wide, so it must be refused by name.
    data = _base_manifest(
        claimed=["r1"],
        divergence=[
            {
                "rule": "r1",
                "kind": "message",
                "ledger": "anchor text here",
                "message": "only this exact wording",
            }
        ],
    )
    with pytest.raises(dc.HarnessError) as excinfo:
        dc.parse_manifest(data, source="<test>")
    assert "does not take ['message']" in str(excinfo.value)


def test_parse_manifest_accepts_a_well_formed_manifest():
    data = _base_manifest(
        claimed=["r1"],
        **{"new-engine": ["python3", "fake.py"]},
        divergence=[{"rule": "r1", "kind": "message", "ledger": "anchor text here"}],
    )
    manifest = dc.parse_manifest(data, source="<test>")
    assert manifest.claimed == ("r1",)
    assert manifest.new_engine == ("python3", "fake.py")
    assert manifest.targets == (dc.Target(name="self", path="."),)
    assert len(manifest.divergences) == 1


# --------------------------------------------------------------------------
# Ledger extraction and reference checking
# --------------------------------------------------------------------------


def test_ledger_section_stops_at_next_heading():
    doc = textwrap.dedent(
        """\
        # Title

        ## Divergence ledger

        - entry one
        - entry two

        ## Something else

        not part of the ledger
        """
    )
    section = dc.ledger_section(doc)
    assert "entry one" in section
    assert "entry two" in section
    assert "not part of the ledger" not in section


def test_ledger_section_raises_when_heading_missing():
    with pytest.raises(dc.HarnessError, match="missing required heading"):
        dc.ledger_section("# Title\n\nno ledger here\n")


def test_verify_ledger_refs_accepts_real_anchor_spanning_a_line_wrap():
    doc = dc.LEDGER_DOC.read_text(encoding="utf-8")
    ledger_text = dc.ledger_section(doc)
    # This phrase spans a real line wrap in dsl-rewrite.md's ledger entry
    # about fix_id, so this also proves whitespace-normalization works.
    divergence = dc.Divergence(
        rule="r1", kind="message", ledger="any current id that deviates from"
    )
    errors = dc.verify_ledger_refs((divergence,), ledger_text)
    assert errors == []


def test_verify_ledger_refs_rejects_a_fabricated_anchor():
    doc = dc.LEDGER_DOC.read_text(encoding="utf-8")
    ledger_text = dc.ledger_section(doc)
    divergence = dc.Divergence(
        rule="r1", kind="message", ledger="this phrase was never written anywhere"
    )
    errors = dc.verify_ledger_refs((divergence,), ledger_text)
    assert len(errors) == 1
    assert "r1" in errors[0]


def test_shipped_manifest_parses_and_ledger_refs_resolve():
    import tomllib

    with dc.DEFAULT_MANIFEST.open("rb") as fh:
        data = tomllib.load(fh)
    manifest = dc.parse_manifest(data, source=str(dc.DEFAULT_MANIFEST))
    doc = dc.LEDGER_DOC.read_text(encoding="utf-8")
    ledger_text = dc.ledger_section(doc)
    assert dc.verify_ledger_refs(manifest.divergences, ledger_text) == []


def test_shipped_manifest_claimed_rules_resolve_in_the_old_engine_registry():
    """The typo guard: a claimed rule name must resolve to a real rule.

    Vacuous while the shipped manifest claims nothing (phase 1); arms the
    moment phase 3 appends its first rule name, because the old engine
    itself silently ignores an unknown rule name (probed: a config naming
    a nonexistent rule produces zero findings and exit 0, which would
    compare empty-against-empty and pass).
    """
    import tomllib

    import pypeeker.check.builtin  # noqa: F401  (side-effect registration)
    from pypeeker.check.rules import get_project_rule, get_rule

    with dc.DEFAULT_MANIFEST.open("rb") as fh:
        data = tomllib.load(fh)
    manifest = dc.parse_manifest(data, source=str(dc.DEFAULT_MANIFEST))
    for rule in manifest.claimed:
        assert get_rule(rule) is not None or get_project_rule(rule) is not None, (
            f"claimed rule {rule!r} does not resolve to a real check rule"
        )


# --------------------------------------------------------------------------
# End-to-end: a tiny real project, a fake new engine
# --------------------------------------------------------------------------


_FAKE_ENGINE = """\
import json
import sys
from pathlib import Path

sentinel = Path(__file__).parent / "ran.marker"
sentinel.write_text("ran")
payload_path = Path(__file__).parent / "payload.json"
print(payload_path.read_text())
"""


def _build_tiny_project(tmp_path: Path) -> Path:
    project = tmp_path / "tiny-project"
    (project / "src" / "pkg").mkdir(parents=True)
    (project / "src" / "pkg" / "__init__.py").write_text('"""A tiny package."""\n')
    (project / "src" / "pkg" / "mod.py").write_text("import os\n")
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.pypeeker]
            src = ["src"]
            """
        )
    )
    return project


def _build_manifest(tmp_path: Path, project: Path, *, claimed: list[str], new_engine_dir: Path) -> Path:
    manifest_path = tmp_path / "manifest.toml"
    claimed_toml = ", ".join(json.dumps(c) for c in claimed)
    new_engine_toml = json.dumps(str(sys.executable)) + ", " + json.dumps(str(new_engine_dir / "fake_engine.py"))
    manifest_path.write_text(
        textwrap.dedent(
            f"""\
            version = 1
            claimed = [{claimed_toml}]
            new-engine = [{new_engine_toml}]

            [[target]]
            name = "tiny"
            path = "{project.as_posix()}"
            """
        )
    )
    return manifest_path


def _write_fake_engine(engine_dir: Path, payload: dict) -> None:
    engine_dir.mkdir(parents=True, exist_ok=True)
    (engine_dir / "fake_engine.py").write_text(_FAKE_ENGINE)
    (engine_dir / "payload.json").write_text(json.dumps(payload))


@pytest.fixture
def tiny_project(tmp_path):
    return _build_tiny_project(tmp_path)


def test_empty_claimed_never_invokes_the_new_engine(tmp_path, tiny_project):
    engine_dir = tmp_path / "engine"
    _write_fake_engine(engine_dir, {"schema": 1, "findings": []})
    manifest_path = _build_manifest(tmp_path, tiny_project, claimed=[], new_engine_dir=engine_dir)

    exit_code = dc.main(["--manifest", str(manifest_path)])

    assert exit_code == 0
    assert not (engine_dir / "ran.marker").exists()


def test_matching_new_engine_output_passes(tmp_path, tiny_project, capsys):
    engine_dir = tmp_path / "engine"
    _write_fake_engine(
        engine_dir,
        {
            "schema": 1,
            "findings": [
                {
                    "rule": "unused-imports",
                    "path": "src/pkg/mod.py",
                    "line": 1,
                    "message": "import 'os' is unused in this module",
                    "confidence": "declared",
                }
            ],
        },
    )
    manifest_path = _build_manifest(
        tmp_path, tiny_project, claimed=["unused-imports"], new_engine_dir=engine_dir
    )

    exit_code = dc.main(["--manifest", str(manifest_path)])

    assert exit_code == 0
    assert (engine_dir / "ran.marker").exists()
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "differential-check: PASS" in out


def test_fabricated_divergence_on_a_claimed_rule_fails_loudly(tmp_path, tiny_project, capsys):
    engine_dir = tmp_path / "engine"
    _write_fake_engine(
        engine_dir,
        {
            "schema": 1,
            "findings": [
                {
                    "rule": "unused-imports",
                    "path": "src/pkg/mod.py",
                    "line": 99,
                    "message": "totally made up",
                    "confidence": "declared",
                }
            ],
        },
    )
    manifest_path = _build_manifest(
        tmp_path, tiny_project, claimed=["unused-imports"], new_engine_dir=engine_dir
    )

    exit_code = dc.main(["--manifest", str(manifest_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "rule=unused-imports" in out
    assert "missing=1" in out
    assert "extra=1" in out
    assert "differential-check: FAIL" in out


def test_two_runs_over_an_unchanged_tree_are_byte_identical(tmp_path, tiny_project, capsys):
    engine_dir = tmp_path / "engine"
    _write_fake_engine(
        engine_dir,
        {
            "schema": 1,
            "findings": [
                {
                    "rule": "unused-imports",
                    "path": "src/pkg/mod.py",
                    "line": 1,
                    "message": "import 'os' is unused in this module",
                    "confidence": "declared",
                }
            ],
        },
    )
    manifest_path = _build_manifest(
        tmp_path, tiny_project, claimed=["unused-imports"], new_engine_dir=engine_dir
    )

    exit_code_1 = dc.main(["--manifest", str(manifest_path), "--json"])
    first = capsys.readouterr().out
    exit_code_2 = dc.main(["--manifest", str(manifest_path), "--json"])
    second = capsys.readouterr().out

    assert exit_code_1 == 0 and exit_code_2 == 0
    assert first == second


# --------------------------------------------------------------------------
# A "finding" divergence sanctions one finding, not a whole (path, line)
# --------------------------------------------------------------------------


def test_finding_divergence_does_not_absorb_a_co_located_undeclared_finding():
    # Co-located findings are ordinary: unused-imports fires per name on
    # `import os, sys`, import-boundaries per imported symbol. One ledger entry
    # must sanction one of them, leaving the other to fail the run.
    old = [
        dc.Finding("r1", "a.py", 1, "declared", "a sanctioned difference"),
        dc.Finding("r1", "a.py", 1, "declared", "b real regression, never declared"),
    ]
    div = dc.Divergence(
        rule="r1", kind="finding", ledger="anchor text", side="old", path="a.py", line=1
    )

    report = dc.compare("t", ("r1",), old, [], (div,))

    (rule,) = report.rules
    assert rule.applied == ("finding:old:a.py:1",)
    # One declaration, one finding removed: the other survives into `missing`
    # and fails the run instead of vanishing into a ledger-backed green.
    assert [f.message for f in rule.missing] == ["b real regression, never declared"]
    assert rule.old_count == 1
    assert dc._is_failing(report)


def test_finding_divergence_message_selects_which_co_located_finding_is_sanctioned():
    old = [
        dc.Finding("r1", "a.py", 1, "declared", "sanctioned wording"),
        dc.Finding("r1", "a.py", 1, "declared", "also present in the new engine"),
    ]
    new = [dc.Finding("r1", "a.py", 1, "declared", "also present in the new engine")]
    div = dc.Divergence(
        rule="r1",
        kind="finding",
        ledger="anchor text",
        side="old",
        path="a.py",
        line=1,
        message="sanctioned wording",
    )

    report = dc.compare("t", ("r1",), old, new, (div,))

    (rule,) = report.rules
    assert rule.missing == () and rule.extra == ()
    assert rule.applied == ("finding:old:a.py:1",)
    assert not dc._is_failing(report)


def test_two_finding_divergences_are_needed_to_sanction_two_co_located_findings():
    old = [
        dc.Finding("r1", "a.py", 1, "declared", "first"),
        dc.Finding("r1", "a.py", 1, "declared", "second"),
    ]
    div = dc.Divergence(
        rule="r1", kind="finding", ledger="anchor text", side="old", path="a.py", line=1
    )

    report = dc.compare("t", ("r1",), old, [], (div, div))

    (rule,) = report.rules
    assert rule.applied == ("finding:old:a.py:1", "finding:old:a.py:1")
    assert rule.missing == ()
    assert rule.old_count == 0
    assert not dc._is_failing(report)


def test_finding_divergence_removes_the_sort_least_match_regardless_of_input_order():
    a = dc.Finding("r1", "a.py", 1, "declared", "aaa first by sort order")
    b = dc.Finding("r1", "a.py", 1, "declared", "zzz last by sort order")
    div = dc.Divergence(
        rule="r1", kind="finding", ledger="anchor text", side="old", path="a.py", line=1
    )

    forward = dc.compare("t", ("r1",), [a, b], [], (div,))
    reversed_ = dc.compare("t", ("r1",), [b, a], [], (div,))

    assert [f.message for f in forward.rules[0].missing] == ["zzz last by sort order"]
    assert [f.message for f in reversed_.rules[0].missing] == ["zzz last by sort order"]


# --------------------------------------------------------------------------
# --work-dir deletes only what the run created
# --------------------------------------------------------------------------


def test_open_work_root_without_an_argument_creates_an_owned_temp_dir():
    work_root, owned = dc._open_work_root(None)
    try:
        assert owned is True
        assert work_root.is_dir()
        assert work_root.name.startswith("differential-check-")
    finally:
        dc._clean_work_root(work_root, owned=True, subdirs=[])


def test_open_work_root_creates_a_missing_work_dir_and_owns_it(tmp_path):
    target = tmp_path / "scratch"

    work_root, owned = dc._open_work_root(str(target))

    assert owned is True
    assert work_root == target and target.is_dir()


def test_open_work_root_borrows_a_pre_existing_empty_dir_without_owning_it(tmp_path):
    target = tmp_path / "scratch"
    target.mkdir()

    work_root, owned = dc._open_work_root(str(target))

    assert owned is False
    assert work_root == target


def test_open_work_root_refuses_a_non_empty_dir_and_leaves_it_intact(tmp_path):
    target = tmp_path / "scratch"
    target.mkdir()
    keeper = target / "IMPORTANT.txt"
    keeper.write_text("do not delete me")

    with pytest.raises(dc.HarnessError) as excinfo:
        dc._open_work_root(str(target))

    assert "already exists and is not empty" in str(excinfo.value)
    assert "IMPORTANT.txt" in str(excinfo.value)
    assert keeper.read_text() == "do not delete me"


def test_open_work_root_refuses_a_path_that_is_not_a_directory(tmp_path):
    target = tmp_path / "not-a-dir"
    target.write_text("file contents")

    with pytest.raises(dc.HarnessError) as excinfo:
        dc._open_work_root(str(target))

    assert "not a directory" in str(excinfo.value)
    assert target.read_text() == "file contents"


def test_clean_work_root_removes_an_owned_dir_whole(tmp_path):
    work_root = tmp_path / "owned"
    (work_root / "tiny").mkdir(parents=True)

    dc._clean_work_root(work_root, owned=True, subdirs=[work_root / "tiny"])

    assert not work_root.exists()


def test_clean_work_root_leaves_a_borrowed_dir_standing_and_empty(tmp_path):
    work_root = tmp_path / "borrowed"
    (work_root / "tiny" / "src").mkdir(parents=True)

    dc._clean_work_root(work_root, owned=False, subdirs=[work_root / "tiny"])

    assert work_root.is_dir()
    assert list(work_root.iterdir()) == []


def test_main_refuses_a_non_empty_work_dir_before_running_either_engine(
    tmp_path, tiny_project, capsys
):
    engine_dir = tmp_path / "engine"
    _write_fake_engine(engine_dir, {"schema": 1, "findings": []})
    manifest_path = _build_manifest(
        tmp_path, tiny_project, claimed=["unused-imports"], new_engine_dir=engine_dir
    )
    work_dir = tmp_path / "my-notes"
    work_dir.mkdir()
    keeper = work_dir / "IMPORTANT.txt"
    keeper.write_text("do not delete me")

    exit_code = dc.main(["--manifest", str(manifest_path), "--work-dir", str(work_dir)])

    assert exit_code == 2
    assert work_dir.is_dir()
    assert keeper.read_text() == "do not delete me"
    assert not (engine_dir / "ran.marker").exists()
    err = capsys.readouterr().err
    assert "differential-check: ERROR:" in err
    assert "already exists and is not empty" in err
