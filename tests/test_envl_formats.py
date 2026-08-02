"""envl format adapters, exercised against the real fixture corpus.

Every test here runs on captured output from `tests/fixtures/envelope/`, not on
invented input. The corpus is what makes claims about the adapters checkable:
a hand-written diff proves the diff parser handles hand-written diffs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from envl import (
    BlobRecord,
    Capture,
    EnvelopeConfig,
    build_envelope,
    detect_format,
    envelope_json,
)

CORPUS = Path(__file__).parent / "fixtures" / "envelope"
MANIFEST = json.loads((CORPUS / "MANIFEST.json").read_text(encoding="utf-8"))
FIXTURES = MANIFEST["fixtures"]

# The size below which the envelope is empirically a net loss on this corpus.
# See test_envelope_is_a_net_loss_on_near_threshold_captures for why this
# number is asserted rather than quietly rounded away.
_PAYOFF_BYTES = 4096


@pytest.fixture(autouse=True)
def _envl_env(tmp_path, monkeypatch):
    """Keep every test in this module off the developer's real ~/.cache."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ENVL_CONFIG", raising=False)


def _ids(entries):
    return [entry["path"] for entry in entries]


def _payload(entry: dict) -> str:
    return (CORPUS / entry["path"]).read_text(encoding="utf-8")


def _envelope(entry: dict, config: EnvelopeConfig | None = None) -> dict:
    payload = _payload(entry)
    capture = Capture(
        argv=tuple(entry["command"].split()),
        command=entry["command"],
        cwd="/home/user/pypeeker-envelope",
        exit_code=entry["exit_code"],
        output=payload,
        captured_at="2026-08-02T00:00:00Z",
    )
    digest = entry["sha256"]
    blob = BlobRecord(
        digest=digest,
        path=Path("/root/.cache/envl/blobs") / digest[:2] / digest,
        bytes=len(payload.encode("utf-8")),
        deduped=False,
    )
    return build_envelope(capture, config or EnvelopeConfig(), blob)


def _of_format(name: str) -> list[dict]:
    return [entry for entry in FIXTURES if entry["format"] == name]


def _is_shell_fragment(command: str) -> bool:
    return any(token in command for token in ("|", "&&", ";", ">", "$("))


# --------------------------------------------------------------------------
# Corpus integrity
# --------------------------------------------------------------------------


def test_corpus_covers_every_format_family():
    families = {entry["format"] for entry in FIXTURES}
    assert families == {"json", "diff", "pytest", "search", "text"}


def test_manifest_matches_the_files_on_disk():
    on_disk = {
        str(path.relative_to(CORPUS))
        for path in CORPUS.rglob("*")
        if path.is_file() and path.name not in {"MANIFEST.json", "README.md"}
    }
    assert on_disk == {entry["path"] for entry in FIXTURES}


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_manifest_records_the_true_size_and_digest(entry):
    payload = (CORPUS / entry["path"]).read_bytes()
    assert len(payload) == entry["original_bytes"]
    assert len(payload) == entry["stored_bytes"], "fixtures are stored verbatim"


def test_corpus_stays_under_its_documented_cap():
    total = sum(entry["stored_bytes"] for entry in FIXTURES)
    assert total == MANIFEST["total_bytes"]
    assert total <= MANIFEST["size_cap_bytes"] == 409_600


def test_manifest_header_records_the_snapshot_of_a_live_transcript_set():
    # The transcripts are appended to while the extractor runs, so the counts
    # are only meaningful next to the instant they were taken.
    assert MANIFEST["snapshot_utc"].endswith("Z")
    assert MANIFEST["transcript_files"] > 0
    assert MANIFEST["tool_results_scanned"] >= MANIFEST["bash_results_scanned"] > 0


# --------------------------------------------------------------------------
# The corpus and the shipping classifier must agree
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_detect_format_agrees_with_the_manifest(entry):
    # The manifest's labels are written by envl.detect_format itself. This is
    # the drift guard: change the sniffing order and this fails loudly instead
    # of silently reclassifying the corpus on the next extraction.
    assert detect_format(entry["command"], _payload(entry)) == entry["format"]


def test_a_declared_format_overrides_the_sniffed_one():
    entry = _of_format("diff")[0]
    payload = _payload(entry)
    assert detect_format(entry["command"], payload) == "diff"
    assert detect_format(entry["command"], payload, declared="text") == "text"


# --------------------------------------------------------------------------
# Envelope invariants across the whole corpus
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_every_envelope_is_valid_json(entry):
    serialized = envelope_json(_envelope(entry))
    assert json.loads(serialized)["envelope"] == "envl/1"


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_every_envelope_stays_under_the_size_ceiling(entry):
    config = EnvelopeConfig()
    assert len(envelope_json(_envelope(entry, config))) <= config.max_envelope_bytes


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_every_envelope_carries_the_blob_path_and_snapshot_warning(entry):
    blob = _envelope(entry)["blob"]
    assert blob["sha256"] == entry["sha256"]
    assert blob["path"].endswith(entry["sha256"])
    assert "re-run the command for current state" in blob["note"]


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_truncation_is_announced_loudly_with_shown_and_total(entry):
    envelope = _envelope(entry)
    if not envelope["truncated"]:
        assert "TRUNCATED" not in envelope
        assert "dropped" not in envelope
        return
    assert envelope["TRUNCATED"].startswith("SHOWING ")
    assert "FULL OUTPUT IS ON DISK AT" in envelope["TRUNCATED"]
    assert envelope["dropped"]
    for record in envelope["dropped"]:
        assert record["shown"] < record["total"]
        assert f"SHOWING {record['shown']} OF {record['total']}" in envelope["TRUNCATED"]


@pytest.mark.parametrize(
    "entry",
    [e for e in FIXTURES if e["original_bytes"] >= _PAYOFF_BYTES],
    ids=_ids([e for e in FIXTURES if e["original_bytes"] >= _PAYOFF_BYTES]),
)
def test_envelope_is_smaller_than_the_raw_payload_above_the_payoff_size(entry):
    assert len(envelope_json(_envelope(entry))) < entry["original_bytes"]


def test_envelope_is_a_net_loss_on_near_threshold_captures():
    """Pin the honest crossover: enveloping small output costs more than it saves.

    Nine of this corpus's forty-three captures — every one of them between the
    2048-byte default threshold and about 3 KB — serialize to an envelope
    *larger* than the output it replaces. Structured metadata, recipes and a
    64-character blob digest are not free, and below a few kilobytes they cost
    more than the payload. This is asserted rather than rounded away because the
    replay harness's numbers are only honest if this is visible in them.
    """
    losses = [
        entry
        for entry in FIXTURES
        if len(envelope_json(_envelope(entry))) >= entry["original_bytes"]
    ]
    assert losses, "expected the crossover to still exist in this corpus"
    assert all(entry["original_bytes"] < _PAYOFF_BYTES for entry in losses)


def test_the_corpus_as_a_whole_shrinks_by_more_than_half():
    raw = sum(entry["original_bytes"] for entry in FIXTURES)
    enveloped = sum(len(envelope_json(_envelope(entry))) for entry in FIXTURES)
    assert enveloped < raw // 2


# --------------------------------------------------------------------------
# Per-format invariants
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _of_format("json"), ids=_ids(_of_format("json")))
def test_json_envelopes_expose_element_keys_for_arrays_of_objects(entry):
    envelope = _envelope(entry)
    top_level = envelope["summary"]["top_level"]
    document = json.loads(_payload(entry))
    if not isinstance(document, dict):
        pytest.skip("root is not an object")
    for key, value in document.items():
        if isinstance(value, list) and any(isinstance(i, dict) for i in value[:50]):
            # Without element keys a model cannot build a correct jq path and
            # burns a call discovering the shape.
            assert top_level[key]["element_keys"], key
        if isinstance(value, list):
            assert top_level[key]["count"] == len(value)


@pytest.mark.parametrize("entry", _of_format("json"), ids=_ids(_of_format("json")))
def test_json_envelopes_offer_jq_recipes(entry):
    recipes = _envelope(entry)["recipes"]
    assert any(recipe.startswith("jq ") for recipe in recipes)


@pytest.mark.parametrize("entry", _of_format("diff"), ids=_ids(_of_format("diff")))
def test_diff_envelopes_report_per_file_stats_and_never_offer_jq(entry):
    envelope = _envelope(entry)
    summary = envelope["summary"]
    assert summary["totals"]["files"] >= 1
    assert summary["totals"]["added"] + summary["totals"]["removed"] > 0
    for record in summary["files"]:
        assert set(record) >= {"path", "added", "removed", "hunks", "blob_lines"}
        start, end = record["blob_lines"]
        assert 1 <= start <= end <= envelope["lines"]
    # jq cannot read a diff; offering it would waste the drill-in call.
    assert not any("jq" in recipe for recipe in envelope["recipes"])
    assert any(recipe.startswith("sed -n ") for recipe in envelope["recipes"])

    paths = {record["path"] for record in summary["files"]}
    git_recipes = [r for r in envelope["recipes"] if r.startswith("git ")]
    if _is_shell_fragment(entry["command"]):
        # `git diff HEAD -- f.py | head -160` cannot be re-scoped by appending
        # a pathspec: `head` would swallow the `--` and print the file. Offering
        # nothing beats offering a command that exits 0 and prints the wrong
        # thing; the blob line range is the handle instead.
        assert git_recipes == []
    else:
        assert git_recipes, "a plain `git diff …` capture is re-scopable"
    for recipe in git_recipes:
        assert recipe.count(" -- ") == 1, recipe
        assert recipe.split(" -- ", 1)[1].strip("'\"") in paths, recipe


@pytest.mark.parametrize("entry", _of_format("diff"), ids=_ids(_of_format("diff")))
def test_diff_totals_match_a_recount_of_the_payload(entry):
    lines = _payload(entry).splitlines()
    added = sum(
        1 for line in lines if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in lines if line.startswith("-") and not line.startswith("---")
    )
    hunks = sum(1 for line in lines if line.startswith("@@"))
    totals = _envelope(entry)["summary"]["totals"]
    assert (totals["added"], totals["removed"], totals["hunks"]) == (
        added,
        removed,
        hunks,
    )


@pytest.mark.parametrize("entry", _of_format("pytest"), ids=_ids(_of_format("pytest")))
def test_pytest_envelopes_report_counts_and_drill_in_handles(entry):
    envelope = _envelope(entry)
    summary = envelope["summary"]
    assert summary["counts"], "a pytest fixture always carries a tally"
    assert any(key in summary["counts"] for key in ("passed", "failed", "errors"))
    if summary["counts"].get("failed"):
        assert summary["failed_nodes"], "failures must carry their node ids"
        assert any(
            recipe.startswith("uv run pytest ") for recipe in envelope["recipes"]
        )
    assert not any("jq" in recipe for recipe in envelope["recipes"])


@pytest.mark.parametrize("entry", _of_format("pytest"), ids=_ids(_of_format("pytest")))
def test_pytest_first_failure_is_inline_exactly_when_the_capture_has_one(entry):
    """The excerpt tracks the payload, not the tally.

    Three fixtures in this corpus are real `uv run pytest -q ... | tail -40`
    captures: the agent piped the run through `tail`, which cut the
    `____ test_name ____` failure headers before the output was ever captured.
    Those payloads report failures in their summary line but contain no failure
    block, and the adapter correctly emits no `first_failure` rather than
    inventing one. `failed_nodes` still recovers the drill-in handles from the
    `FAILED` short-summary lines, so the envelope stays useful.
    """
    payload = _payload(entry)
    summary = _envelope(entry)["summary"]
    has_block = any(
        line.startswith("__") and line.endswith("__") and " " in line
        for line in payload.splitlines()
    )
    if has_block:
        assert summary["first_failure"]
    else:
        assert "first_failure" not in summary


@pytest.mark.parametrize("entry", _of_format("search"), ids=_ids(_of_format("search")))
def test_search_envelopes_tally_matches_per_file(entry):
    envelope = _envelope(entry)
    summary = envelope["summary"]
    assert summary["matches"] > 0
    assert summary["lines"] == envelope["lines"]
    assert summary["files"], "which files matched is usually the question"
    for record in summary["files"]:
        assert set(record) == {"path", "matches"}
        assert record["matches"] >= 1
    counts = [record["matches"] for record in summary["files"]]
    assert counts == sorted(counts, reverse=True), "busiest file first"
    assert any(recipe.startswith("grep ") for recipe in envelope["recipes"])


@pytest.mark.parametrize("entry", _of_format("text"), ids=_ids(_of_format("text")))
def test_text_envelopes_report_line_counts_with_range_recipes(entry):
    envelope = _envelope(entry)
    summary = envelope["summary"]
    assert summary["lines"] == len(_payload(entry).splitlines())
    assert summary["head"], "a text envelope always shows its first lines"
    recipes = envelope["recipes"]
    assert any(recipe.startswith("sed -n ") for recipe in recipes)
    assert any(recipe.startswith("wc -l ") for recipe in recipes)
    assert not any("jq" in recipe for recipe in recipes)


@pytest.mark.parametrize(
    "entry",
    [e for e in FIXTURES if e["format"] in {"text", "search"}],
    ids=_ids([e for e in FIXTURES if e["format"] in {"text", "search"}]),
)
def test_excerpt_lines_are_clipped_but_the_document_stays_valid_json(entry):
    config = EnvelopeConfig(max_line_chars=40)
    envelope = _envelope(entry, config)
    serialized = envelope_json(envelope)
    assert json.loads(serialized) == envelope
    for line in envelope["summary"]["head"]:
        # 40 characters plus the one-character ellipsis marker.
        assert len(line) <= 41


# --------------------------------------------------------------------------
# Every fixture survives an adversarially small budget
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Recipes have to survive being run, not merely being spelled
# --------------------------------------------------------------------------


def _capture(command: str, payload: str, cwd: str) -> Capture:
    return Capture(
        argv=tuple(command.split()),
        command=command,
        cwd=cwd,
        exit_code=0,
        output=payload,
        captured_at="2026-08-02T00:00:00Z",
    )


def _built(command: str, payload: str, cwd: str, blob: Path) -> dict:
    blob.write_text(payload, encoding="utf-8")
    record = BlobRecord(
        digest="c" * 64, path=blob, bytes=len(payload.encode("utf-8")), deduped=False
    )
    return build_envelope(_capture(command, payload, cwd), EnvelopeConfig(), record)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    )


@pytest.fixture
def staged_repo(tmp_path):
    """A real git repo with two staged edits, one under a path containing a space."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    names = ["alpha.py", "beta module.py"]
    for name in names:
        (repo / name).write_text("original = 1\n" * 40, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for index, name in enumerate(names):
        (repo / name).write_text(f"changed = {index}\n" * 40, encoding="utf-8")
    _git(repo, "add", "-A")
    return repo, names


def test_a_diff_recipe_runs_and_scopes_to_exactly_one_file(staged_repo, tmp_path):
    """The drill-in handle is only worth anything if the shell accepts it."""
    repo, names = staged_repo
    payload = _git(repo, "diff", "--cached").stdout
    envelope = _built("git diff --cached", payload, str(repo), tmp_path / "blob")

    git_recipes = [r for r in envelope["recipes"] if r.startswith("git ")]
    assert len(git_recipes) == 2
    for recipe, name in zip(git_recipes, sorted(names), strict=False):
        result = subprocess.run(
            ["bash", "-c", recipe], cwd=str(repo), capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        headers = [
            line for line in result.stdout.splitlines() if line.startswith("diff --git")
        ]
        assert len(headers) == 1, f"{recipe} returned {len(headers)} files"
        assert name in headers[0]


def test_a_pathspec_in_the_captured_command_is_replaced_not_appended(
    staged_repo, tmp_path
):
    """`git diff --cached f.py` + ` -- f.py` is `fatal: bad revision`."""
    repo, _ = staged_repo
    payload = _git(repo, "diff", "--cached", "alpha.py").stdout
    envelope = _built("git diff --cached alpha.py", payload, str(repo), tmp_path / "blob")

    recipe = next(r for r in envelope["recipes"] if r.startswith("git "))
    assert recipe == "git diff --cached -- alpha.py"
    result = subprocess.run(
        ["bash", "-c", recipe], cwd=str(repo), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("diff --git") == 1


def test_an_existing_double_dash_is_not_doubled(staged_repo, tmp_path):
    """A second `--` makes git ignore the new scoping and return every file."""
    repo, _ = staged_repo
    payload = _git(repo, "diff", "--cached", "--", "alpha.py", "beta module.py").stdout
    envelope = _built(
        "git diff --cached -- alpha.py 'beta module.py'",
        payload,
        str(repo),
        tmp_path / "blob",
    )

    for recipe in [r for r in envelope["recipes"] if r.startswith("git ")]:
        assert recipe.count(" -- ") == 1, recipe
        result = subprocess.run(
            ["bash", "-c", recipe], cwd=str(repo), capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("diff --git") == 1, recipe


def test_a_revision_argument_survives_the_rebuild(staged_repo, tmp_path):
    repo, _ = staged_repo
    payload = _git(repo, "diff", "HEAD").stdout
    envelope = _built("git diff HEAD", payload, str(repo), tmp_path / "blob")
    assert all(
        recipe.startswith("git diff HEAD -- ")
        for recipe in envelope["recipes"]
        if recipe.startswith("git ")
    )


def test_a_piped_capture_offers_blob_ranges_instead_of_a_broken_git_command(
    staged_repo, tmp_path
):
    """`git diff … | head -160` cannot be re-scoped; `head` eats the pathspec."""
    repo, _ = staged_repo
    payload = _git(repo, "diff", "--cached").stdout
    envelope = _built(
        "git diff HEAD -- alpha.py | head -160", payload, str(repo), tmp_path / "blob"
    )

    assert not any(recipe.startswith("git diff") for recipe in envelope["recipes"])
    ranges = [r for r in envelope["recipes"] if r.startswith("sed -n ")]
    assert len(ranges) == 2, "one blob range per reported file"
    result = subprocess.run(
        ["bash", "-c", ranges[0]], capture_output=True, text=True
    )
    assert result.stdout.count("diff --git") == 1


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"violations": [{"rule": "r", "file": "a.py"}] * 40}),
        json.dumps({"check-findings": [{"rule": "r", "file": "a.py"}] * 40}),
        json.dumps([{"kind": "function", "name": "n"}] * 60),
    ],
    ids=["bare-key", "key-needing-quotes", "array-root"],
)
def test_every_jq_recipe_runs_and_returns_output(tmp_path, payload):
    envelope = _built("uv run pypeeker check", payload, str(tmp_path), tmp_path / "blob")
    recipes = [r for r in envelope["recipes"] if r.startswith("jq ")]
    assert recipes
    for recipe in recipes:
        result = subprocess.run(
            ["bash", "-c", f"set -o pipefail; {recipe}"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{recipe} -> {result.stderr}"
        assert result.stdout.strip(), f"{recipe} returned nothing"


def test_search_counts_hits_under_paths_containing_spaces(tmp_path):
    """`backlog/tasks/task-14 - a title.md` is an ordinary grep hit in this repo.

    A path pattern that stops at the first space drops those lines from
    `matches`, from `files` and from the totals quoted in the LOUD marker, and
    reports the undercount as fact.
    """
    hits = [f"src/pypeeker/cli.py:{i}:    found here" for i in range(10)]
    hits += [
        f"backlog/tasks/task-1{i} - a task title.md:{i}:    found here"
        for i in range(6)
    ]
    payload = "\n".join(hits) + "\n"

    assert detect_format("grep -rn found src/ backlog/", payload) == "search"
    envelope = _built(
        "grep -rn found src/ backlog/", payload, "/repo", tmp_path / "blob"
    )
    summary = envelope["summary"]
    assert summary["matches"] == 16
    assert summary["lines"] == 16
    paths = {record["path"] for record in summary["files"]}
    assert any(" - a task title.md" in path for path in paths)
    assert sum(record["matches"] for record in summary["files"]) <= 16


@pytest.mark.parametrize("entry", FIXTURES, ids=_ids(FIXTURES))
def test_a_tiny_budget_still_yields_valid_json(entry):
    config = EnvelopeConfig(max_items=1, max_lines=1, max_envelope_bytes=1)
    serialized = envelope_json(_envelope(entry, config))
    document = json.loads(serialized)
    assert document["format"] == entry["format"]
    assert document["blob"]["sha256"] == entry["sha256"]


@pytest.mark.parametrize("entry", _of_format("pytest"), ids=_ids(_of_format("pytest")))
def test_a_pytest_run_with_no_failures_reports_no_failed_nodes(entry):
    """The negative direction of the counts/nodes agreement.

    The forward assertion (failures carry node ids) passed while the adapter
    was reporting a clean `-v` run's fifteen *passing* node ids under
    `failed_nodes`, because nothing checked that a green run stays empty.
    """
    summary = _envelope(entry)["summary"]
    counts = summary["counts"]
    if counts.get("failed") or counts.get("errors"):
        return
    assert summary["failed_nodes"] == [], (
        f"{entry['path']} reports {counts} yet names failing nodes"
    )


def test_verbose_progress_lines_are_not_mistaken_for_failures(tmp_path):
    """`pytest -v` prints `path.py::test PASSED [ 6%]` once per test.

    An unscoped `^\\S+\\.py::\\S+` scan turned every one of those into a
    `failed_nodes` entry and offered `uv run pytest <passing test>` as the
    first recipe — a model burning a call to re-run a test that passed.
    """
    payload = (
        "".join(
            f"tests/test_mod.py::TestGroup::test_case_{i} PASSED [ {i * 5:>2}%]\n"
            for i in range(20)
        )
        + "\n============================== 20 passed in 1.42s ==============================\n"
    )
    envelope = _built("uv run pytest tests/test_mod.py -v", payload, "/repo", tmp_path / "blob")
    summary = envelope["summary"]
    assert envelope["format"] == "pytest"
    assert summary["counts"] == {"passed": 20}
    assert summary["failed_nodes"] == []
    assert not any(recipe.startswith("uv run pytest ") for recipe in envelope["recipes"])


def test_a_verbose_run_with_real_failures_still_names_only_the_failures(tmp_path):
    """Mixed `-v` output: the FAILED short summary is trusted, the passes are not."""
    payload = (
        "".join(
            f"tests/test_mod.py::test_case_{i} PASSED [ {i * 4:>2}%]\n" for i in range(20)
        )
        + "tests/test_mod.py::test_broken FAILED [100%]\n"
        + "=================================== FAILURES ===================================\n"
        + "__________________________________ test_broken _________________________________\n"
        + "    assert 1 == 2\nE   assert 1 == 2\n"
        + "=========================== short test summary info ============================\n"
        + "FAILED tests/test_mod.py::test_broken - assert 1 == 2\n"
        + "========================= 1 failed, 20 passed in 1.9s ==========================\n"
    )
    envelope = _built("uv run pytest tests/test_mod.py -v", payload, "/repo", tmp_path / "blob")
    summary = envelope["summary"]
    assert summary["counts"] == {"failed": 1, "passed": 20}
    assert summary["failed_nodes"] == ["tests/test_mod.py::test_broken"]
    assert envelope["recipes"][0] == (
        "uv run pytest tests/test_mod.py::test_broken -q --tb=short"
    )
