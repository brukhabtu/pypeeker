"""The new engine's runnable surface: the config it reads, the JSON it prints.

``scripts/differential-check.py`` is unforgiving about the payload shape — it
rejects a finding whose key set is not exactly the five it compares — and it is
equally unforgiving about behaviour, since a config difference between the two
engines shows up as a parity failure with no hint of its cause. Both are pinned
here.
"""

import json

import pytest

from pypeeker.dsl.differential import SCHEMA, _NoIndexError, _read_config, _run

TUPLES = """\
def go():
    a = [1, 2]
    for v in a:
        print(v)
"""


# ---------------------------------------------------------------------------
# config: the slice of check/config.py a ported rule can observe
# ---------------------------------------------------------------------------


def test_a_target_without_a_pyproject_falls_back_to_the_default_src_root(tmp_path):
    assert _read_config(tmp_path) == (("src",), {})


def test_a_pyproject_without_a_pypeeker_section_falls_back_too(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert _read_config(tmp_path) == (("src",), {})


def test_src_roots_come_from_the_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pypeeker]\nsrc = ["lib", "app"]\n')
    src, _ = _read_config(tmp_path)
    assert src == ("lib", "app")


def test_rule_subtables_become_option_tables_and_reserved_keys_do_not(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        'rules = ["require-docstrings"]\n'
        'plugins = []\n'
        "\n"
        "[tool.pypeeker.require-docstrings]\n"
        'kinds = ["function"]\n'
    )
    _, options = _read_config(tmp_path)
    assert options == {"require-docstrings": {"kinds": ["function"]}}


def test_the_project_wide_visibility_table_is_injected_into_every_enabled_rule(tmp_path):
    # check/config.py copies [tool.pypeeker.visibility] into each enabled
    # rule's options under the reserved key "visibility". A port that skips
    # this disagrees with the old engine on exactly the projects that declare
    # the section — including this repository.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings", "prefer-tuple"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'allow-decorators = ["public_api"]\n'
    )
    _, options = _read_config(tmp_path)
    assert options == {
        "require-docstrings": {"visibility": {"allow-decorators": ["public_api"]}},
        "prefer-tuple": {"visibility": {"allow-decorators": ["public_api"]}},
    }


def test_an_explicit_rule_option_wins_over_the_injected_visibility_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'allow-decorators = ["public_api"]\n'
        "\n"
        "[tool.pypeeker.require-docstrings]\n"
        'visibility = ["private"]\n'
    )
    _, options = _read_config(tmp_path)
    assert options["require-docstrings"]["visibility"] == ["private"]


def test_a_rule_that_is_not_enabled_gets_no_injected_options(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["prefer-tuple"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'allow-decorators = ["public_api"]\n'
    )
    _, options = _read_config(tmp_path)
    assert set(options) == {"prefer-tuple"}


# ---------------------------------------------------------------------------
# the payload the harness parses
# ---------------------------------------------------------------------------


def test_run_emits_the_harness_payload_with_exactly_the_five_compared_keys(indexed_project):
    project, _ = indexed_project({"src/app.py": TUPLES})
    payload = _run(project, ("prefer-tuple",))
    assert payload["schema"] == SCHEMA == 1
    (finding,) = payload["findings"]
    assert set(finding) == {"rule", "path", "line", "message", "confidence"}
    assert finding == {
        "rule": "prefer-tuple",
        "path": "src/app.py",
        "line": 2,
        "message": "list 'a' is never mutated — consider a tuple",
        "confidence": "declared",
    }


def test_payload_paths_stay_relative_to_the_target(indexed_project):
    project, _ = indexed_project({"src/app.py": TUPLES})
    for finding in _run(project, ("prefer-tuple",))["findings"]:
        assert not finding["path"].startswith("/")


def test_the_payload_is_json_serializable(indexed_project):
    project, _ = indexed_project({"src/app.py": TUPLES})
    assert json.loads(json.dumps(_run(project, ("prefer-tuple",))))["schema"] == 1


def test_no_requested_rules_means_no_findings_not_every_rule(indexed_project):
    project, _ = indexed_project({"src/app.py": TUPLES})
    assert _run(project, ()) == {"schema": 1, "findings": []}


def test_src_roots_from_the_target_pyproject_filter_the_corpus(indexed_project):
    project, _ = indexed_project({"src/app.py": TUPLES})
    (project / "pyproject.toml").write_text('[tool.pypeeker]\nsrc = ["nowhere"]\n')
    assert _run(project, ("prefer-tuple",))["findings"] == []


def test_an_explicit_empty_src_list_is_not_the_default_root(tmp_path):
    # check/config.py defaults only when the key is ABSENT
    # (section.get("src", list(DEFAULT_SRC))), and its engine skips the prefix
    # filter entirely for an empty tuple. Coercing [] to ("src",) here would
    # silently under-report on any project that writes src = [].
    (tmp_path / "pyproject.toml").write_text("[tool.pypeeker]\nsrc = []\n")
    src, _ = _read_config(tmp_path)
    assert src == ()


def test_an_explicit_empty_src_list_checks_every_indexed_file(indexed_project):
    project, _ = indexed_project({"src/app.py": TUPLES, "other/app.py": TUPLES})
    (project / "pyproject.toml").write_text("[tool.pypeeker]\nsrc = []\n")

    paths = [f["path"] for f in _run(project, ("prefer-tuple",))["findings"]]

    assert paths == ["other/app.py", "src/app.py"]


# ---------------------------------------------------------------------------
# "read nothing" is not "found nothing"
# ---------------------------------------------------------------------------


def test_a_target_with_no_index_is_refused_rather_than_reported_as_zero_findings(tmp_path):
    # An empty findings list from an unindexed target grades PASS against every
    # rule whose old-side count is also 0 — a green comparison against an
    # engine that read nothing at all.
    unindexed = tmp_path / "project"
    (unindexed / "src").mkdir(parents=True)
    (unindexed / "src" / "app.py").write_text(TUPLES)

    with pytest.raises(_NoIndexError) as excinfo:
        _run(unindexed, ("prefer-tuple",))

    assert "holds no index" in str(excinfo.value)


def test_a_target_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(_NoIndexError) as excinfo:
        _run(tmp_path / "nowhere", ("prefer-tuple",))

    assert "not a directory" in str(excinfo.value)


def test_the_refusal_does_not_depend_on_any_rule_being_requested(tmp_path):
    # The zero-rule payload is the one an empty claimed list produces, so the
    # guard has to fire before the rule loop, not inside it.
    with pytest.raises(_NoIndexError):
        _run(tmp_path, ())


def test_an_indexed_target_whose_src_roots_match_nothing_is_still_a_real_corpus(indexed_project):
    # The mirror image of the refusal above: an index exists and was read, the
    # configured roots just select no file. The old engine reports zero
    # findings there too, so this must stay a payload, not an error.
    project, _ = indexed_project({"src/app.py": TUPLES})
    (project / "pyproject.toml").write_text('[tool.pypeeker]\nsrc = ["nowhere"]\n')

    assert _run(project, ("prefer-tuple",)) == {"schema": SCHEMA, "findings": []}
