"""Tests for the replay harness's arithmetic (scripts/replay-envelope.py).

The counterfactual in ``.claude/workflows/ENVELOPE-COUNTERFACTUAL.md`` is only
worth what its arithmetic is worth, and the two ways that arithmetic could
flatter the envelope are both silent: projecting onto raw fixture bytes instead
of per-family baseline totals, and charging below-threshold pass-through output
at zero instead of full price. Both are pinned here.

The harness lives in ``scripts/`` (a dev tool, deliberately outside ``src/``),
so it is loaded by path rather than imported as a package.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HARNESS_PATH = Path(__file__).resolve().parent.parent / "scripts" / "replay-envelope.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("replay_envelope", _HARNESS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_envelope"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


@pytest.fixture(autouse=True)
def _envl_env(tmp_path, monkeypatch):
    """Keep every run off the developer's real cache and config."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ENVL_CONFIG", raising=False)


def test_break_even_solves_r_plus_d_phi_equals_one():
    # r = 0.5 with phi = 0.25: a drill-in costs a quarter of the original, so
    # two drill-ins per envelope are needed to give back the half we saved.
    assert harness.break_even_rate(0.5, 0.25) == pytest.approx(2.0)
    # At the worst-case phi = 1.0 the bar collapses to d* = 1 - r.
    assert harness.break_even_rate(0.5, 1.0) == pytest.approx(0.5)


def test_break_even_is_negative_when_the_envelope_is_already_a_net_loss():
    assert harness.break_even_rate(1.05, 0.25) < 0
    assert harness._break_even_text(harness.break_even_rate(1.05, 0.25)) == "never"


def test_break_even_text_marks_ratios_that_pay_at_any_drill_in_rate():
    assert harness._break_even_text(harness.break_even_rate(0.09, 0.25)) == ">1.00"
    assert harness._break_even_text(0.16) == "0.16"


def _baseline(tokens: int, above_share: float, family: str = "git") -> dict:
    return {
        "total_tokens": 1_000_000,
        "families": {
            family: {
                "tokens": tokens,
                "calls": 10,
                "above_tokens": int(tokens * above_share),
                "above_share": above_share,
            }
        },
    }


def test_projection_charges_below_threshold_output_at_full_price():
    """``(1 - a_f)`` of a family never reaches the envelope and must cost 1.0x."""
    baseline = _baseline(tokens=100_000, above_share=0.5)
    corpus = {"git": {"ratio": 0.0, "fixtures": 3}}

    result = harness.project(baseline, corpus, 0.5, ["git"], [0.0], 0.25)

    # Even with a hypothetically free envelope (r_f = 0), only the above-threshold
    # half can be saved: the rest passes through untouched.
    assert result["projected"]["0"] == pytest.approx(50_000.0)
    assert result["saved"]["0"] == pytest.approx(50_000.0)


def test_projection_uses_baseline_totals_not_raw_fixture_bytes():
    """B_f, not the corpus's own byte total, is what the ratio is applied to."""
    baseline = _baseline(tokens=1_164_000, above_share=0.926)
    corpus = {"git": {"ratio": 0.09, "fixtures": 7}}

    result = harness.project(baseline, corpus, 0.5, ["git"], [0.0, 0.5], 0.25)

    expected_d0 = 1_164_000 * ((1 - 0.926) + 0.926 * 0.09)
    expected_d5 = 1_164_000 * ((1 - 0.926) + 0.926 * (0.09 + 0.5 * 0.25))
    assert result["projected"]["0"] == pytest.approx(expected_d0)
    assert result["projected"]["0.5"] == pytest.approx(expected_d5)
    # The drill-in penalty must make the saving strictly worse, never better.
    assert result["saved"]["0.5"] < result["saved"]["0"]


def test_projection_flags_imputed_families_and_reports_their_weight():
    baseline = _baseline(tokens=80_000, above_share=0.6, family="ls/find")
    result = harness.project(baseline, {}, 0.36, ["ls/find"], [0.0], 0.25)

    row = result["families"][0]
    assert row["imputed"] is True
    assert row["corpus_fixtures"] == 0
    assert row["ratio"] == pytest.approx(0.36)
    assert result["imputed_share"] == pytest.approx(1.0)


def test_split_material_families_rolls_up_the_singleton_tail():
    rows = [
        {"family": "git", "baseline_tokens": 900, "imputed": False},
        {"family": "sed/awk", "baseline_tokens": 90, "imputed": False},
        {"family": "X=/tmp/a", "baseline_tokens": 5, "imputed": True},
        {"family": "Y=/tmp/b", "baseline_tokens": 5, "imputed": True},
    ]

    shown, tail = harness.split_material_families(rows, floor_share=0.01)

    assert [row["family"] for row in shown] == ["git", "sed/awk"]
    assert tail == {"families": 2, "baseline_tokens": 10, "imputed": 2}


def test_band_spans_the_drill_in_rates_rather_than_quoting_one_number():
    scope = {"saved_share_of_total": {"0": 0.162, "0.2": 0.153, "0.5": 0.138}}
    assert harness._band(scope, [0.0, 0.2, 0.5]) == "13.8%–16.2%"


def _write_fixture(root: Path, name: str, payload: str, command: str) -> dict:
    (root / "text").mkdir(parents=True, exist_ok=True)
    (root / "text" / name).write_text(payload, encoding="utf-8")
    return {
        "path": f"text/{name}",
        "command": command,
        "command_family": "sed/awk",
        "format": "text",
        "original_bytes": len(payload.encode("utf-8")),
        "stored_bytes": len(payload.encode("utf-8")),
        "exit_code": 0,
        "sha256": "ab" * 32,
    }


def test_replay_charges_a_below_threshold_fixture_at_exactly_its_own_cost(tmp_path):
    """A pass-through must not be scored as a saving; before and after must match."""
    from envl import load_config

    tiny = "one short line\n"
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(
            {"fixtures": [_write_fixture(tmp_path, "tiny.txt", tiny, "sed -n 1p f")]}
        ),
        encoding="utf-8",
    )

    rows = harness.replay(manifest_path, load_config())

    assert rows[0]["enveloped"] is False
    assert rows[0]["before_tokens"] == rows[0]["after_tokens"]
    assert rows[0]["valid_json"] is None


def test_replay_envelopes_a_large_fixture_into_smaller_valid_json(tmp_path):
    from envl import load_config

    big = "".join(f"line {index} of a long captured file\n" for index in range(400))
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(
            {"fixtures": [_write_fixture(tmp_path, "big.txt", big, "sed -n 1,400p f")]}
        ),
        encoding="utf-8",
    )

    rows = harness.replay(manifest_path, load_config())

    assert rows[0]["enveloped"] is True
    assert rows[0]["valid_json"] is True
    assert rows[0]["after_tokens"] < rows[0]["before_tokens"]


def _transcript(root: Path, calls: list[tuple[str, str]]) -> str:
    """Write one synthetic transcript holding ``(command, payload)`` Bash calls."""
    folder = root / "wf_test"
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, (command, payload) in enumerate(calls):
        lines.append(
            json.dumps(
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": f"t{index}",
                                "name": "Bash",
                                "input": {"command": command},
                            }
                        ]
                    }
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"t{index}",
                                "content": payload,
                            }
                        ]
                    }
                }
            )
        )
    (folder / "agent-1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(root)


def test_the_ratio_is_measured_on_the_population_not_a_sample(tmp_path):
    """Every above-threshold result is replayed; nothing is sampled.

    Harness v1 projected ratios measured on the largest-first fixture corpus.
    The ratio is size-dependent, so that overstated every scope; v2 replays the
    whole above-threshold population instead, and this is what pins it.
    """
    from envl import load_config

    big = "".join(f"src/pkg/mod.py:{i}:    a matching line here\n" for i in range(200))
    small = "two\nshort\nlines\n"
    directory = _transcript(
        tmp_path / "transcripts",
        [("grep -rn thing src/", big)] * 3 + [("grep -rn thing src/", small)],
    )

    baseline = harness.measure_baseline(directory, load_config())

    population = baseline["population"]
    assert population["overall"]["results"] == 3, "the small result is below threshold"
    family = population["families"]["grep/rg"]
    assert family["results"] == 3
    assert 0.0 < family["ratio"] < 1.0
    assert family["source"] == "population"
    # a_f charges the below-threshold call at full price.
    assert baseline["families"]["grep/rg"]["above_share"] < 1.0


def test_a_family_whose_envelope_is_bigger_is_reported_as_a_net_loss(tmp_path):
    """A ratio above 1.0 has to survive to the report, not be smoothed away."""
    from envl import load_config

    # Just over the 2048-byte threshold, in lines short enough to be quoted in
    # full: the envelope's metadata, recipes and 64-character digest are pure
    # addition. This is the band the population table reports as net-loss.
    payload = "".join("x" * 350 + "\n" for _ in range(6))
    directory = _transcript(tmp_path / "transcripts", [("uv run pytest -q", payload)])

    population = harness.measure_baseline(directory, load_config())["population"]

    family = population["families"]["uv run pytest"]
    assert family["results"] == 1
    assert family["after"] > family["before"], "the envelope is bigger than the output"
    assert family["ratio"] > 1.0
    assert family["net_loss_share"] == pytest.approx(1.0)
    assert harness.break_even_rate(family["ratio"], 0.25) < 0
    assert harness._break_even_text(harness.break_even_rate(family["ratio"], 0.25)) == (
        "never"
    )


@pytest.mark.parametrize(
    ("payload_bytes", "label"),
    [(2048, "2-4 KB"), (4096, "4-8 KB"), (8192, "8-16 KB"), (100_000, ">16 KB")],
)
def test_size_buckets_split_the_population_where_the_ratio_turns(payload_bytes, label):
    assert harness._bucket_of(payload_bytes) == label


def test_corpus_versus_population_reports_the_gap_worst_first():
    corpus = {
        "git": {"ratio": 0.08, "fixtures": 7},
        "uv run pytest": {"ratio": 0.59, "fixtures": 5},
    }
    population = {
        "git": {"ratio": 0.16, "results": 456, "before": 1_046_341},
        "uv run pytest": {"ratio": 1.14, "results": 59, "before": 40_647},
    }

    rows = harness._corpus_vs_population(corpus, population)

    assert [row["family"] for row in rows] == ["uv run pytest", "git"]
    assert rows[0]["gap"] == pytest.approx(0.55)
    assert all(row["gap"] > 0 for row in rows), "the corpus flatters, in both families"


def test_projection_labels_a_population_measured_ratio_as_such():
    baseline = _baseline(tokens=100_000, above_share=0.9)
    ratios = {"git": {"ratio": 0.16, "fixtures": 456, "source": "population"}}

    result = harness.project(baseline, ratios, 0.5, ["git"], [0.0], 0.25)

    assert result["families"][0]["source"] == "population"
    assert result["families"][0]["imputed"] is False


def test_replay_does_not_write_blobs_into_the_cache(tmp_path, monkeypatch):
    """The harness must never touch the real cache; the blob path is synthesised."""
    from envl import load_config

    cache = tmp_path / "cache"
    monkeypatch.setenv("ENVL_CACHE_DIR", str(cache))
    big = "".join(f"line {index}\n" for index in range(500))
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(
            {"fixtures": [_write_fixture(tmp_path, "b.txt", big, "sed -n 1,500p f")]}
        ),
        encoding="utf-8",
    )

    harness.replay(manifest_path, load_config())

    assert not cache.exists()
