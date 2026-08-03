"""envl envelope: threshold pass-through, structural truncation, valid JSON always."""

from __future__ import annotations

import json

import pytest

from envl import (
    BlobRecord,
    Capture,
    EnvelopeConfig,
    build_envelope,
    detect_format,
    envelope_json,
    should_envelope,
)


@pytest.fixture(autouse=True)
def _envl_env(tmp_path, monkeypatch):
    """Keep every test in this module off the developer's real ~/.cache."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ENVL_CONFIG", raising=False)


def _capture(output: str, command: str = "some command") -> Capture:
    return Capture(
        argv=tuple(command.split()),
        command=command,
        cwd="/repo",
        exit_code=0,
        output=output,
        captured_at="2026-01-01T00:00:00Z",
    )


def _blob(tmp_path, capture: Capture) -> BlobRecord:
    path = tmp_path / "blob"
    path.write_text(capture.output, encoding="utf-8")
    return BlobRecord(
        digest="d" * 64,
        path=path,
        bytes=len(capture.output.encode("utf-8")),
        deduped=False,
    )


def _build(tmp_path, output: str, command: str = "some command", **kwargs):
    capture = _capture(output, command)
    config = EnvelopeConfig(cache_dir=tmp_path / "cache", **kwargs)
    return build_envelope(capture, config, _blob(tmp_path, capture))


def _synthetic_diff(files: int, hunk_lines: int = 4) -> str:
    parts = []
    for index in range(files):
        parts.append(f"diff --git a/pkg/mod{index}.py b/pkg/mod{index}.py")
        parts.append("index 1111111..2222222 100644")
        parts.append(f"--- a/pkg/mod{index}.py")
        parts.append(f"+++ b/pkg/mod{index}.py")
        parts.append("@@ -1,3 +1,4 @@")
        for line in range(hunk_lines):
            parts.append(f"+added line {line} in module {index}")
        parts.append("-removed line")
    return "\n".join(parts) + "\n"


def test_below_threshold_does_not_envelope():
    config = EnvelopeConfig(threshold_bytes=2048)
    assert should_envelope(_capture("x" * 2047), config) is False
    assert should_envelope(_capture("x" * 2048), config) is True


def test_kill_switch_disables_enveloping_regardless_of_size():
    config = EnvelopeConfig(enabled=False, threshold_bytes=1)
    assert should_envelope(_capture("x" * 100_000), config) is False


def test_envelope_is_valid_json_and_carries_capture_metadata(tmp_path):
    envelope = _build(tmp_path, "line\n" * 200, "sed -n 1,200p file.txt")
    parsed = json.loads(envelope_json(envelope))
    assert parsed["envelope"] == "envl/1"
    assert parsed["command"] == "sed -n 1,200p file.txt"
    assert parsed["cwd"] == "/repo"
    assert parsed["exit_code"] == 0
    assert parsed["captured_at"] == "2026-01-01T00:00:00Z"
    assert parsed["format"] == "text"
    assert parsed["lines"] == 200
    assert parsed["blob"]["path"] == str(tmp_path / "blob")
    assert "re-run the command for current state" in parsed["blob"]["note"]


def test_untruncated_envelope_omits_the_loud_marker(tmp_path):
    envelope = _build(tmp_path, "one\ntwo\nthree\n", max_lines=30)
    assert envelope["truncated"] is False
    assert "TRUNCATED" not in envelope
    assert "dropped" not in envelope


def test_truncated_envelope_shouts_shown_and_total(tmp_path):
    envelope = _build(tmp_path, "line\n" * 500, max_lines=10)
    assert envelope["truncated"] is True
    marker = envelope["TRUNCATED"]
    assert marker.startswith("SHOWING 10 OF 500")
    assert "490 DROPPED" in marker
    assert str(tmp_path / "blob") in marker
    assert envelope["dropped"] == [
        {"field": "summary.head", "shown": 10, "total": 500}
    ]


def test_truncation_is_structural_not_a_string_slice(tmp_path):
    envelope = _build(tmp_path, "line\n" * 500, max_lines=7)
    head = envelope["summary"]["head"]
    assert len(head) == 7
    assert head[-1] == "line"
    assert json.loads(envelope_json(envelope))["summary"]["head"] == head


@pytest.mark.parametrize("max_lines", [1, 2, 3, 5, 8, 13, 30, 100])
def test_envelope_json_round_trips_at_every_truncation_level(tmp_path, max_lines):
    envelope = _build(tmp_path, "x" * 900 + "\n" + "line\n" * 400, max_lines=max_lines)
    assert json.loads(envelope_json(envelope)) == envelope


def test_long_lines_are_clipped_with_an_ellipsis(tmp_path):
    envelope = _build(tmp_path, ("q" * 5000 + "\n") * 20, max_line_chars=50)
    first = envelope["summary"]["head"][0]
    assert len(first) == 51
    assert first.endswith("…")


def test_shrink_loop_keeps_a_900_file_diff_under_the_ceiling(tmp_path):
    envelope = _build(
        tmp_path,
        _synthetic_diff(900),
        "git diff --cached",
        max_envelope_bytes=4096,
        max_items=8,
    )
    serialized = envelope_json(envelope)
    assert len(serialized) <= 4096
    assert json.loads(serialized) == envelope
    assert envelope["format"] == "diff"
    assert envelope["summary"]["totals"]["files"] == 900
    assert envelope["truncated"] is True


def test_envelope_is_far_smaller_than_the_payload(tmp_path):
    payload = _synthetic_diff(120)
    envelope = _build(tmp_path, payload, "git diff --cached")
    assert len(envelope_json(envelope)) < len(payload) // 4


def test_diff_recipes_never_offer_jq(tmp_path):
    envelope = _build(tmp_path, _synthetic_diff(5), "git diff --cached")
    assert envelope["format"] == "diff"
    assert all("jq" not in recipe for recipe in envelope["recipes"])
    assert "git diff --cached -- pkg/mod0.py" in envelope["recipes"]


def test_diff_summary_carries_per_file_added_removed_hunks(tmp_path):
    envelope = _build(tmp_path, _synthetic_diff(3, hunk_lines=6), "git diff")
    files = envelope["summary"]["files"]
    assert [f["path"] for f in files] == [f"pkg/mod{i}.py" for i in range(3)]
    assert files[0]["added"] == 6
    assert files[0]["removed"] == 1
    assert files[0]["hunks"] == 1
    assert envelope["summary"]["totals"] == {
        "files": 3,
        "added": 18,
        "removed": 3,
        "hunks": 3,
    }


def test_json_summary_exposes_element_keys_for_arrays_of_objects(tmp_path):
    payload = json.dumps(
        {
            "violations": [
                {"file": f"src/m{i}.py", "line": i, "rule": "r"} for i in range(60)
            ],
            "summary": {"total": 60},
        }
    )
    envelope = _build(tmp_path, payload, "uv run pypeeker check", max_items=3)
    top = envelope["summary"]["top_level"]
    assert top["violations"]["type"] == "array"
    assert top["violations"]["count"] == 60
    assert top["violations"]["element_keys"] == ["file", "line", "rule"]
    assert len(envelope["summary"]["sample"]["violations"]) == 3
    assert any(r.startswith("jq '.violations[0:20]'") for r in envelope["recipes"])


def test_a_wide_root_object_is_truncated_by_width_and_says_so(tmp_path):
    """Width is truncated like length: a 700-key root cannot silently pass.

    Nothing in the envelope's shape metadata may be unbounded. A root object
    with one key per file (a path-to-size map, a per-symbol index) used to be
    described key-by-key, producing an envelope *larger* than the payload it
    replaced while reporting `truncated: false` — the exact combination the
    envelope exists to rule out.
    """
    payload = json.dumps({f"src/pkg/mod{i}.py": i for i in range(700)})
    envelope = _build(tmp_path, payload, "uv run pypeeker index src")

    serialized = envelope_json(envelope)
    assert len(serialized) <= 4096
    assert len(serialized) < len(payload)
    assert envelope["truncated"] is True
    assert "SHOWING 32 OF 700 in summary.top_level (668 DROPPED)" in envelope["TRUNCATED"]
    assert len(envelope["summary"]["top_level"]) == 32


def test_a_wide_nested_object_reports_shown_versus_present_keys(tmp_path):
    payload = json.dumps({"symbols": {f"src/m{i}.py:S{i}:local": i for i in range(5000)}})
    envelope = _build(tmp_path, payload, "uv run pypeeker query symbols")

    assert len(envelope_json(envelope)) <= 4096
    assert envelope["truncated"] is True
    assert "SHOWING 32 OF 5000 in summary object keys" in envelope["TRUNCATED"]
    described = envelope["summary"]["top_level"]["symbols"]
    assert described["count"] == 5000, "the true total is still reported"
    assert len(described["element_keys"]) == 32


def test_wide_sample_elements_mark_the_keys_they_dropped(tmp_path):
    payload = json.dumps([{f"k{j}": j for j in range(400)} for _ in range(50)])
    envelope = _build(tmp_path, payload, "uv run pypeeker check --json")

    assert len(envelope_json(envelope)) <= 4096
    element = envelope["summary"]["sample"]["root"][0]
    assert element["…"].startswith("+"), "a clipped sample element says so inline"
    assert envelope["truncated"] is True


def test_an_array_root_gets_runnable_jq_paths_not_a_fake_key(tmp_path):
    """`.(root)` is not a jq program; `.[0:20]` is.

    The failure mode this pins is the quiet one: `jq -r '.(root)[].kind' blob
    | sort -u` exits 0 with empty output because the pipe masks jq's exit 3, and
    a model reads empty output as "no values exist".
    """
    payload = json.dumps([{"kind": "function", "line": i} for i in range(80)])
    envelope = _build(tmp_path, payload, "uv run pypeeker query --json")

    assert "(root)" not in envelope_json(envelope)
    assert envelope["summary"]["root"]["type"] == "array"
    assert envelope["summary"]["root"]["count"] == 80
    blob = str(tmp_path / "blob")
    assert f"jq '.[0:20]' {blob}" in envelope["recipes"]
    assert f"jq -r '.[] | .kind' {blob} | sort -u" in envelope["recipes"]


def test_a_top_level_key_needing_quotes_is_bracketed_in_jq_recipes(tmp_path):
    payload = json.dumps({"check-findings": [{"rule": "r", "file": "a.py"}] * 40})
    envelope = _build(tmp_path, payload, "uv run pypeeker check")

    blob = str(tmp_path / "blob")
    assert f"""jq '.["check-findings"][0:20]' {blob}""" in envelope["recipes"]
    assert not any("'.check-findings" in recipe for recipe in envelope["recipes"])


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({f"path/to/f{i}.py": {"bytes": i} for i in range(400)}),
        json.dumps([{f"col{j}": j for j in range(200)} for _ in range(30)]),
        json.dumps({"findings": [{"rule": "r" * 200, "file": "f" * 200}] * 200}),
        json.dumps({f"field_{i}": [{"a" * 60: i, "b" * 60: i}] * 200 for i in range(40)}),
        json.dumps({"k" * 400 + str(i): ["v" * 200] * 50 for i in range(50)}),
        json.dumps({"ключ" * 50: ["значение" * 50] * 50}),
    ],
    ids=[
        "wide-root",
        "wide-elements",
        "long-strings",
        "many-arrays",
        "long-keys",
        "multibyte-keys-and-values",
    ],
)
def test_no_json_shape_can_push_the_envelope_over_the_ceiling(tmp_path, payload):
    envelope = _build(tmp_path, payload, "uv run pypeeker check", max_envelope_bytes=4096)
    serialized = envelope_json(envelope)
    # The ceiling is named in bytes, so it is enforced in bytes: a summary of
    # Cyrillic output is two bytes per character and would slip a char-counted
    # ceiling.
    assert len(serialized.encode("utf-8")) <= 4096
    assert json.loads(serialized) == envelope
    assert len(serialized) < len(payload)


def test_a_key_too_long_to_show_is_never_used_to_build_a_jq_path(tmp_path):
    """An abbreviated key names nothing; `jq … | sort -u` would say so at exit 0."""
    payload = json.dumps(
        {"x" * 500: [{"kind": "a"}] * 40, "findings": [{"rule": "r"}] * 40}
    )
    envelope = _build(tmp_path, payload, "uv run pypeeker check")

    recipes = envelope["recipes"]
    assert any(recipe.startswith("jq '.findings[0:20]'") for recipe in recipes)
    assert not any("xxxx" in recipe for recipe in recipes)
    assert any("…" in key for key in envelope["summary"]["top_level"])
    # An abbreviated name is a different loss from a dropped key, and the marker
    # has to say which one happened.
    assert {"field": "summary key names not abbreviated", "shown": 19, "total": 20} in (
        envelope["dropped"]
    )


def test_pytest_summary_keeps_counts_and_first_failure(tmp_path):
    payload = (
        "=================================== FAILURES ===================================\n"
        "______________________ test_alpha ______________________\n"
        "    assert 1 == 2\n"
        "E   assert 1 == 2\n"
        "______________________ test_beta ______________________\n"
        "    assert 3 == 4\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_x.py::test_alpha - assert 1 == 2\n"
        "FAILED tests/test_x.py::test_beta - assert 3 == 4\n"
        "2 failed, 41 passed in 1.23s\n"
    )
    envelope = _build(tmp_path, payload, "uv run pytest -q", max_lines=20)
    summary = envelope["summary"]
    assert envelope["format"] == "pytest"
    assert summary["counts"] == {"failed": 2, "passed": 41}
    assert summary["duration_s"] == 1.23
    assert summary["failed_nodes"][:2] == [
        "tests/test_x.py::test_alpha",
        "tests/test_x.py::test_beta",
    ]
    assert "    assert 1 == 2" in summary["first_failure"]
    assert any("test_alpha" in recipe for recipe in envelope["recipes"])


def test_search_summary_counts_matches_per_file(tmp_path):
    payload = "".join(
        f"src/pkg/mod{i % 3}.py:{i}:    hit number {i}\n" for i in range(90)
    )
    envelope = _build(tmp_path, payload, "grep -rn hit src", max_items=2)
    summary = envelope["summary"]
    assert envelope["format"] == "search"
    assert summary["matches"] == 90
    assert len(summary["files"]) == 2
    assert summary["files"][0]["matches"] == 30
    assert all("jq" not in recipe for recipe in envelope["recipes"])


def test_registry_declared_format_overrides_content_sniffing(tmp_path):
    from envl import CommandRule

    payload = "[project]\nname = \"pypeeker\"\n" + "line\n" * 100
    config = EnvelopeConfig(
        cache_dir=tmp_path / "cache",
        commands=(CommandRule(match="sed *", format="text"),),
    )
    capture = _capture(payload, "sed -n 1,200p pyproject.toml")
    envelope = build_envelope(capture, config, _blob(tmp_path, capture))
    assert envelope["format"] == "text"


def test_toml_output_is_not_sniffed_as_json():
    payload = '[project]\nname = "pypeeker"\nversion = "0.1.0"\n'
    assert detect_format("sed -n 1,200p pyproject.toml", payload) == "text"


def test_strict_json_parse_drives_json_detection():
    assert detect_format("cmd", '{"a": [1, 2]}') == "json"
    assert detect_format("cmd", '{"a": [1, 2]') == "text"


def test_dropped_excerpts_are_announced_as_shown_zero(tmp_path):
    """The last-resort rebuild must not delete an excerpt silently.

    A 300k-character single line under a tight ceiling forces
    `_drop_excerpts`. Before, the head was removed and the envelope still said
    `truncated: false` with no marker, so a model read an amputated summary as
    a complete one.
    """
    envelope = _build(
        tmp_path, "x" * 300_000 + "\n", "cat dist/bundle.min.js", max_envelope_bytes=512
    )
    assert "head" not in envelope["summary"]
    assert envelope["truncated"] is True
    assert envelope["dropped"] == [{"field": "summary.head", "shown": 0, "total": 1}]
    assert envelope["TRUNCATED"].startswith(
        "SHOWING 0 OF 1 in summary.head (1 DROPPED)"
    )


def test_dropped_excerpt_rewrites_its_own_truncation_record(tmp_path):
    """A dropped key that already had a record must not keep claiming `shown: 8`.

    Reporting `SHOWING 30 OF 500 in summary.head` for a key that is no longer
    in the document tells a model to read something that is not there.
    """
    envelope = _build(
        tmp_path, ("z" * 300 + "\n") * 500, "cat big.txt", max_envelope_bytes=400
    )
    assert set(envelope["summary"]) == {"lines"}
    assert envelope["dropped"] == [
        {"field": "summary.head", "shown": 0, "total": 500},
        {"field": "summary.tail", "shown": 0, "total": 5},
    ]
    assert "SHOWING 0 OF 500 in summary.head (500 DROPPED)" in envelope["TRUNCATED"]
    assert json.loads(envelope_json(envelope)) == envelope


def test_declared_diff_format_on_a_stat_payload_falls_back_to_text(tmp_path):
    """`git diff --stat` matches a `git diff*` rule but has no `diff --git` markers.

    Trusting the declaration produced `totals.files: 0`, `files: []` and
    `truncated: false` — a confident "nothing changed" over 60 lines of real
    per-file stat sitting unread on the blob.
    """
    from envl import CommandRule

    payload = (
        "".join(f" src/pkg/mod{i}.py | {i + 3} +++---\n" for i in range(60))
        + " 60 files changed, 300 insertions(+), 120 deletions(-)\n"
    )
    config = EnvelopeConfig(
        cache_dir=tmp_path / "cache",
        commands=(CommandRule(match="git diff*", format="diff", max_items=6),),
    )
    capture = _capture(payload, "git diff --stat")
    envelope = build_envelope(capture, config, _blob(tmp_path, capture))
    assert envelope["format"] == "text"
    assert envelope["summary"]["lines"] == 61
    assert envelope["summary"]["head"][0] == " src/pkg/mod0.py | 3 +++---"
    assert envelope["truncated"] is True
    assert "SHOWING 30 OF 61 in summary.head" in envelope["TRUNCATED"]


def test_declared_json_format_on_a_non_json_payload_falls_back_to_text(tmp_path):
    """A `parse_error` summary with zero recipes is a dead end, not a summary."""
    from envl import CommandRule

    payload = "not json at all\n" * 400
    config = EnvelopeConfig(
        cache_dir=tmp_path / "cache",
        commands=(CommandRule(match="pypeeker*", format="json"),),
    )
    capture = _capture(payload, "pypeeker query symbols")
    envelope = build_envelope(capture, config, _blob(tmp_path, capture))
    assert envelope["format"] == "text"
    assert "parse_error" not in envelope["summary"]
    assert envelope["summary"]["lines"] == 400
    assert envelope["recipes"]
    assert all("jq" not in recipe for recipe in envelope["recipes"])


def test_a_real_diff_is_still_summarized_as_a_diff(tmp_path):
    """The plausibility fallback must not demote adapters that did their job."""
    envelope = _build(tmp_path, _synthetic_diff(5), "git diff --cached")
    assert envelope["format"] == "diff"
    assert envelope["summary"]["totals"]["files"] == 5
