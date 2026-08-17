"""Tests for the differential oracle's FIX pass (scripts/differential-check.py).

Phase 4's half of the harness. ``tests/test_dsl_differential_harness.py`` pins
the findings pass — the text parser, the JSON parser, the ledger-anchored
divergence bookkeeping — and is left untouched; this file pins everything the
fix pass added on top: the ``fix-engine`` and ``fix-divergence`` manifest keys,
the two fix payload parsers, and the comparison over an ordered fix list.

Kept as a separate module rather than appended to its sibling for the reason the
comparison itself exists: the findings pass is the graded read half and its test
module is the record of what phase 3 measured. A new pass gets a new file.

The harness lives in ``scripts/`` (a dev tool, deliberately outside ``src/``),
so it is loaded by path rather than imported as a package — same idiom as its
sibling, and the loader is re-run here so this module stands alone.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
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


def _base_manifest(**overrides) -> dict:
    """The smallest manifest that parses: no claims, one trivial target."""
    data = {
        "version": 1,
        "claimed": [],
        "target": [{"name": "self", "path": "."}],
    }
    data.update(overrides)
    return data



def _fix_manifest(**overrides) -> dict:
    data = _base_manifest(claimed=["unused-imports"])
    data.update(overrides)
    return data


def test_parse_manifest_accepts_a_fix_engine_command():
    manifest = dc.parse_manifest(
        _fix_manifest(**{"fix-engine": ["python", "run.py"]}), source="<test>"
    )
    assert manifest.fix_engine == ("python", "run.py")


def test_parse_manifest_defaults_fix_engine_to_none_so_the_fix_pass_is_opt_in():
    # A manifest that names no fix engine runs the findings pass alone. That is
    # what every harness self-test manifest wants, and what keeps the fix pass
    # from silently grading nothing against nothing on a fabricated manifest.
    assert dc.parse_manifest(_fix_manifest(), source="<test>").fix_engine is None


def test_parse_manifest_rejects_an_empty_fix_engine():
    with pytest.raises(dc.HarnessError, match="'fix-engine' must be a non-empty list"):
        dc.parse_manifest(_fix_manifest(**{"fix-engine": []}), source="<test>")


def test_parse_manifest_rejects_a_fix_divergence_for_an_unclaimed_rule():
    # A fix id is `<rule>:<mutation>:<anchor>`, so its head segment is a rule
    # name — and a divergence may only be declared for a rule the manifest
    # claims, exactly as at the findings layer.
    data = _fix_manifest(**{
        "fix-divergence": [
            {"fix_id": "no-such-rule:remove:x", "side": "new", "ledger": "anchor text"}
        ]
    })
    with pytest.raises(dc.HarnessError, match="not in 'claimed'"):
        dc.parse_manifest(data, source="<test>")


def test_parse_manifest_rejects_a_fix_divergence_without_a_side():
    data = _fix_manifest(**{
        "fix-divergence": [{"fix_id": "unused-imports:remove:x", "ledger": "anchor text"}]
    })
    with pytest.raises(dc.HarnessError, match="needs 'side'"):
        dc.parse_manifest(data, source="<test>")


def test_parse_manifest_rejects_a_fix_divergence_with_a_short_ledger_anchor():
    data = _fix_manifest(**{
        "fix-divergence": [
            {"fix_id": "unused-imports:remove:x", "side": "old", "ledger": "short"}
        ]
    })
    with pytest.raises(dc.HarnessError, match="needs a 'ledger' anchor"):
        dc.parse_manifest(data, source="<test>")


def test_a_fix_divergence_is_ledger_checked_like_any_other():
    # verify_ledger_refs is shared, and reads `.rule` off the declaration. A
    # FixDivergence derives that from its fix id's head segment, so a
    # fabricated anchor is rejected with the same message.
    divergence = dc.FixDivergence(
        fix_id="unused-imports:remove:x",
        side="new",
        ledger="this sentence is nowhere in the ledger",
    )
    errors = dc.verify_ledger_refs((divergence,), "some ledger text")
    assert len(errors) == 1
    assert "unused-imports" in errors[0]


_OLD_FIX_REPORT = json.dumps({
    "fixes": [
        {"fix_id": "r:remove:a", "description": "drop a", "violation": "f.py:1: [r] a"}
    ],
    "skipped_conflicts": [
        {"fix_id": "r:remove:b", "description": "drop b", "violation": "f.py:1: [r] b"}
    ],
    "declined": [{"fix_id": "r:remove:c", "reason": "ambiguous", "detail": "two of them"}],
    "residual_violations": 3,
    "tx_id": "deadbeef",
})

_OLD_TRANSACTION = json.dumps({
    "header": {"tx_id": "deadbeef"},
    "edits": [
        {
            "file": "f.py",
            "start": 0,
            "end": 4,
            "old": "aaaa",
            "new": "bbbb",
            "file_hash": "0" * 64,
            "op": "replace",
        }
    ],
})


def _new_fix_report(**overrides) -> str:
    payload = {
        "schema": 1,
        "fixes": [
            {"fix_id": "r:remove:a", "description": "drop a", "violation": "f.py:1: [r] a"}
        ],
        "skipped_conflicts": [
            {"fix_id": "r:remove:b", "description": "drop b", "violation": "f.py:1: [r] b"}
        ],
        "declined": [
            {"fix_id": "r:remove:c", "reason": "ambiguous", "detail": "two of them"}
        ],
        "edits": [{"file": "f.py", "start": 0, "end": 4, "replacement": "bbbb"}],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_the_two_fix_payloads_normalize_to_the_same_thing():
    # The old side speaks two documents (report + transaction) and the new side
    # one; normalizing both to a FixPayload is what makes them comparable, and
    # the transaction's `new` is the new side's `replacement`.
    old = dc.parse_old_fix_output(_OLD_FIX_REPORT, _OLD_TRANSACTION, scrub=[])
    new = dc.parse_new_fix_output(_new_fix_report(), scrub=[])
    assert old == new


def test_a_planned_nothing_run_reads_as_an_empty_edit_list_not_an_unread_one():
    report = json.dumps({
        "fixes": [],
        "skipped_conflicts": [],
        "declined": [],
        "residual_violations": 0,
        "tx_id": None,
    })
    assert dc.parse_old_fix_output(report, None, scrub=[]).edits == ()


def test_the_new_fix_payload_rejects_an_unknown_key():
    with pytest.raises(dc.HarnessError, match="wrong keys"):
        dc.parse_new_fix_output(_new_fix_report(bogus=1), scrub=[])


def test_the_new_fix_payload_rejects_an_absolute_edit_path():
    payload = _new_fix_report(
        edits=[{"file": "/abs/f.py", "start": 0, "end": 1, "replacement": "x"}]
    )
    with pytest.raises(dc.HarnessError, match="absolute path"):
        dc.parse_new_fix_output(payload, scrub=[])


def _compare(new_text: str, divergences=()):
    return dc.compare_fixes(
        "t",
        dc.parse_old_fix_output(_OLD_FIX_REPORT, _OLD_TRANSACTION, scrub=[]),
        dc.parse_new_fix_output(new_text, scrub=[]),
        divergences,
    )


def test_identical_fix_payloads_compare_clean():
    assert _compare(_new_fix_report()).differences == ()


def test_a_missing_repair_is_reported_as_a_fixes_difference():
    report = _compare(_new_fix_report(fixes=[], edits=[]))
    assert len(report.differences) == 1
    assert "fixes differ" in report.differences[0]


def test_only_the_first_disagreeing_comparison_is_reported():
    # Dropping the repair also drops its edits; reporting both would render one
    # root cause as two symptoms, so the comparison stops at the first.
    report = _compare(_new_fix_report(fixes=[], edits=[], declined=[]))
    assert len(report.differences) == 1
    assert report.differences[0].startswith("fixes differ")


def test_a_reworded_violation_line_fails_even_when_the_fix_ids_agree():
    # This is what regrades the finding's message and confidence tier at the
    # fix layer: a repair attached to the wrong row keeps its id and changes
    # its violation text.
    report = _compare(
        _new_fix_report(
            fixes=[
                {
                    "fix_id": "r:remove:a",
                    "description": "drop a",
                    "violation": "f.py:9: [r] somewhere else",
                }
            ]
        )
    )
    assert report.differences and report.differences[0].startswith("violation differs")


def test_a_changed_edit_byte_range_fails_even_when_everything_else_agrees():
    report = _compare(
        _new_fix_report(edits=[{"file": "f.py", "start": 0, "end": 9, "replacement": "bbbb"}])
    )
    assert report.differences and report.differences[0].startswith("edits differ")


def test_a_declined_reason_change_is_a_difference():
    report = _compare(
        _new_fix_report(
            declined=[{"fix_id": "r:remove:c", "reason": "stale-index", "detail": "x"}]
        )
    )
    assert report.differences and report.differences[0].startswith("declined differ")


def test_a_declared_fix_divergence_removes_exactly_one_repair():
    divergence = dc.FixDivergence(
        fix_id="r:remove:c", side="old", ledger="a real anchor here"
    )
    report = _compare(_new_fix_report(declined=[]), (divergence,))
    assert report.differences == ()
    assert report.applied == ("fix:old:r:remove:c",)


def test_a_stale_fix_divergence_is_an_error_not_a_silent_pass():
    divergence = dc.FixDivergence(
        fix_id="r:remove:nothing", side="old", ledger="a real anchor here"
    )
    report = _compare(_new_fix_report(), (divergence,))
    assert report.unused == ("fix:old:r:remove:nothing",)
    assert report.differences and "stale fix divergence" in report.differences[0]


def test_a_fix_divergence_scoped_to_another_target_does_not_apply():
    divergence = dc.FixDivergence(
        fix_id="r:remove:c", side="old", ledger="a real anchor here", target="other"
    )
    report = _compare(_new_fix_report(declined=[]), (divergence,))
    assert report.applied == ()
    assert report.differences and report.differences[0].startswith("declined differ")


# --------------------------------------------------------------------------
# The missing-fix-engine guard on *this repo's* manifest
#
# The fix pass is opt-in per manifest, which every fabricated self-test
# manifest above depends on. On scripts/parity-manifest.toml that opt-in is a
# single line, and losing it skips the entire write half while the run still
# prints PASS and exits 0 — the same vacuous green the 'claimed' guard makes
# unreachable for the read half. These pin the matching guard: scoped to that
# one file, escapable only with an explicit flag, and firing before either
# engine starts.
# --------------------------------------------------------------------------

_MARKER_ENGINE = """\
from pathlib import Path

Path(__file__).parent.joinpath("ran.marker").write_text("ran")
print('{"schema": 1, "findings": []}')
"""


@pytest.fixture
def marker_engine(tmp_path):
    """A new-engine command that leaves ``ran.marker`` behind when it runs."""
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    (engine_dir / "engine.py").write_text(_MARKER_ENGINE)
    return engine_dir


@pytest.fixture
def no_fix_engine_manifest(tmp_path, marker_engine):
    """A claiming manifest, one real target, and no ``fix-engine`` key.

    Stands in for scripts/parity-manifest.toml with its ``fix-engine`` line
    deleted: everything else about the file is well-formed, so the findings
    pass would run and the run would end in PASS.
    """
    import textwrap

    project = tmp_path / "tiny-project"
    (project / "src" / "pkg").mkdir(parents=True)
    (project / "src" / "pkg" / "__init__.py").write_text('"""A tiny package."""\n')
    (project / "src" / "pkg" / "mod.py").write_text("import os\n")
    (project / "pyproject.toml").write_text("[tool.pypeeker]\nsrc = [\"src\"]\n")

    manifest_path = tmp_path / "manifest.toml"
    new_engine = ", ".join(
        json.dumps(part) for part in (str(sys.executable), str(marker_engine / "engine.py"))
    )
    manifest_path.write_text(
        textwrap.dedent(
            f"""\
            version = 1
            claimed = ["unused-imports"]
            new-engine = [{new_engine}]

            [[target]]
            name = "tiny"
            path = "{project.as_posix()}"
            """
        )
    )
    return manifest_path


def test_this_repos_manifest_naming_no_fix_engine_is_an_error(
    no_fix_engine_manifest, marker_engine, capsys, monkeypatch
):
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", no_fix_engine_manifest)

    exit_code = dc.main([])

    assert exit_code == 2
    assert not (marker_engine / "ran.marker").exists()
    captured = capsys.readouterr()
    assert "names no fix engine ('fix-engine' is absent)" in captured.err
    assert "--allow-no-fix-engine" in captured.err
    assert "PASS" not in captured.out


def test_the_missing_fix_engine_guard_survives_an_unnormalized_manifest_path(
    tmp_path, no_fix_engine_manifest, monkeypatch
):
    # Same file, reached by a path that needs resolving: naming the manifest
    # explicitly is not a way around the guard.
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", tmp_path / "elsewhere" / ".." / "manifest.toml")

    assert dc.main(["--manifest", str(no_fix_engine_manifest)]) == 2


def test_a_fabricated_manifest_without_a_fix_engine_still_runs_findings_only(
    tmp_path, no_fix_engine_manifest, marker_engine, capsys, monkeypatch
):
    # The guard must not fire on manifests that are not this repo's own: the
    # opt-in fix pass is what every fabricated harness manifest relies on.
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", tmp_path / "not-our-manifest.toml")

    exit_code = dc.main(["--manifest", str(no_fix_engine_manifest)])

    assert exit_code != 2
    assert (marker_engine / "ran.marker").exists()
    assert "names no fix engine" not in capsys.readouterr().err


def test_an_explicit_fix_engine_flag_satisfies_the_guard(
    tmp_path, no_fix_engine_manifest, capsys, monkeypatch
):
    # The guard is about the fix pass actually running, not about the manifest
    # key specifically — supplying the command on the CLI is enough.
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", no_fix_engine_manifest)
    stub = tmp_path / "fix-engine.py"
    stub.write_text(
        "import json, sys\n"
        "print(json.dumps({'schema': 1, 'fixes': [], 'skipped_conflicts': [], "
        "'declined': [], 'edits': []}))\n"
    )

    exit_code = dc.main(["--fix-engine", f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}"])

    assert exit_code != 2
    assert "names no fix engine" not in capsys.readouterr().err


def test_allow_no_fix_engine_restores_the_findings_only_run(
    no_fix_engine_manifest, marker_engine, capsys, monkeypatch
):
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", no_fix_engine_manifest)

    exit_code = dc.main(["--allow-no-fix-engine"])

    assert exit_code != 2
    assert (marker_engine / "ran.marker").exists()
    assert "names no fix engine" not in capsys.readouterr().err


def test_the_shipped_manifest_names_a_fix_engine():
    """The guard's premise: pypeeker's real manifest grades repairs in phase 4."""
    import tomllib

    with dc.DEFAULT_MANIFEST.open("rb") as fh:
        data = tomllib.load(fh)
    manifest = dc.parse_manifest(data, source=str(dc.DEFAULT_MANIFEST))

    assert manifest.fix_engine, (
        "scripts/parity-manifest.toml must name a 'fix-engine' in phase 4 — "
        "without it the write half is skipped and the oracle passes vacuously"
    )
