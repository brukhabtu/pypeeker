"""The empty-claim guard on *this repo's* parity manifest (the one CI grades).

Phase 1's acceptance smoke test was an oracle run over a manifest claiming
nothing: it materializes no target, starts neither engine, and prints PASS.
That path still exists and every harness self-test that fabricates a manifest
depends on it. What phase 3 has to make unreachable is a *vacuous* PASS from
``scripts/parity-manifest.toml`` itself — the file
``scripts/differential-check.py`` reads when CI and ``scripts/verify-repo.sh``
invoke it with no ``--manifest`` — because that file now claims real rules, so
an empty ``claimed`` list there means it was emptied or failed to load, not
that parity holds.

The guard is therefore scoped to that one file, and these tests stand in for
it being emptied by repointing ``DEFAULT_MANIFEST`` at a fabricated
empty-claim manifest. They live in their own module rather than in
``test_dsl_differential_harness.py`` so the pre-existing harness suite is not
reopened; the harness is loaded by path here for the same reason it is
there — it is a dev tool in ``scripts/``, deliberately outside ``src/``, so it
is not importable as a package.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parent.parent / "scripts" / "differential-check.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("differential_check_empty_claim", _HARNESS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["differential_check_empty_claim"] = module
    spec.loader.exec_module(module)
    return module


dc = _load_harness()


# A new engine that records the fact it was started. Every test below asserts
# the marker is absent: an empty claim must short-circuit before either engine
# is invoked, whether it ends in the guard's error or in the allowed pass.
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
def tiny_project(tmp_path):
    """The smallest indexable project a manifest target can point at."""
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


@pytest.fixture
def empty_claim_manifest(tmp_path, tiny_project, marker_engine):
    """A well-formed manifest whose ``claimed`` list is empty."""
    manifest_path = tmp_path / "manifest.toml"
    new_engine = ", ".join(
        json.dumps(part) for part in (str(sys.executable), str(marker_engine / "engine.py"))
    )
    manifest_path.write_text(
        textwrap.dedent(
            f"""\
            version = 1
            claimed = []
            new-engine = [{new_engine}]

            [[target]]
            name = "tiny"
            path = "{tiny_project.as_posix()}"
            """
        )
    )
    return manifest_path


def test_this_repos_manifest_claiming_nothing_is_an_error(
    empty_claim_manifest, marker_engine, capsys, monkeypatch
):
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", empty_claim_manifest)

    exit_code = dc.main([])

    assert exit_code == 2
    assert not (marker_engine / "ran.marker").exists()
    captured = capsys.readouterr()
    assert "claims no rules (claimed = [])" in captured.err
    assert "--allow-empty-claim" in captured.err
    assert "PASS" not in captured.out


def test_this_repos_manifest_is_guarded_even_when_named_explicitly(
    tmp_path, empty_claim_manifest, monkeypatch
):
    # Same file, reached by an unnormalized path: the guard resolves both
    # sides, so naming it with --manifest is not a way around it.
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", tmp_path / "elsewhere" / ".." / "manifest.toml")

    assert dc.main(["--manifest", str(empty_claim_manifest)]) == 2


def test_a_fabricated_manifest_claiming_nothing_is_still_trivially_green(
    tmp_path, empty_claim_manifest, marker_engine, capsys, monkeypatch
):
    # The guard must not fire on manifests that are not this repo's own — the
    # harness's own self-tests fabricate empty-claim manifests on purpose.
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", tmp_path / "not-our-manifest.toml")

    exit_code = dc.main(["--manifest", str(empty_claim_manifest)])

    assert exit_code == 0
    assert not (marker_engine / "ran.marker").exists()
    assert "differential-check: PASS (0 claimed rules; nothing to compare)" in capsys.readouterr().out


def test_allow_empty_claim_restores_the_vacuous_pass_report(
    empty_claim_manifest, marker_engine, capsys, monkeypatch
):
    monkeypatch.setattr(dc, "DEFAULT_MANIFEST", empty_claim_manifest)

    exit_code = dc.main(["--allow-empty-claim"])

    assert exit_code == 0
    assert not (marker_engine / "ran.marker").exists()
    assert (
        "differential-check: PASS (0 claimed rules; nothing to compare)"
        in capsys.readouterr().out
    )


def test_the_shipped_manifest_claims_at_least_one_rule():
    """The guard's premise: pypeeker's real manifest is never empty in phase 3."""
    with dc.DEFAULT_MANIFEST.open("rb") as fh:
        data = tomllib.load(fh)
    manifest = dc.parse_manifest(data, source=str(dc.DEFAULT_MANIFEST))

    assert manifest.claimed, "scripts/parity-manifest.toml must claim rules in phase 3"
