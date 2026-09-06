"""The engine's runnable surface over an on-disk target: "read nothing" is not "found nothing".

:func:`pypeeker.dsl.engine._run` opens a target directory, reads its
``[tool.pypeeker]`` config and runs the named rules over the index it finds
there. The interesting behaviour is the refusal: a target that carries no index
would otherwise answer "zero findings", which is indistinguishable from a clean
project and is the wrong answer to give a caller who asked what is wrong with
the code. These tests pin that guard and the source-root filtering around it.

The guard tests moved from ``tests/test_dsl_differential_runner.py`` when the
differential oracle was retired; the payload-shape assertions the harness
needed went with it.
"""

import pytest

from pypeeker.dsl.engine import SCHEMA, _NoIndexError, _run

TUPLES = """\
def go():
    a = [1, 2]
    for v in a:
        print(v)
"""


def test_a_target_with_no_index_is_refused_rather_than_reported_as_zero_findings(tmp_path):
    # Zero findings from an unindexed target reads as a clean project. It is
    # not: nothing was read at all, so the run has no answer to give.
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
    # A zero-rule run produces an empty payload on its own, so the guard has to
    # fire before the rule loop, not inside it.
    with pytest.raises(_NoIndexError):
        _run(tmp_path, ())


def test_an_indexed_target_whose_src_roots_match_nothing_is_still_a_real_corpus(indexed_project):
    # The mirror image of the refusal above: an index exists and was read, the
    # configured roots just select no file. That is a real "found nothing", so
    # it must stay a payload, not an error.
    project, _ = indexed_project({"src/app.py": TUPLES})
    (project / "pyproject.toml").write_text('[tool.pypeeker]\nsrc = ["nowhere"]\n')

    assert _run(project, ("prefer-tuple",)) == {"schema": SCHEMA, "findings": []}


def test_no_requested_rules_means_no_findings_not_every_rule(indexed_project):
    # An empty rule tuple is a request for nothing, not a request for
    # everything: the run must not quietly fall back to the whole registry over
    # a corpus that does have something to say.
    project, _ = indexed_project({"src/app.py": TUPLES})

    assert _run(project, ()) == {"schema": SCHEMA, "findings": []}


def test_an_explicit_empty_src_list_checks_every_indexed_file(indexed_project):
    # The behavioural half of the ``src = []`` distinction
    # ``tests/test_dsl_config.py`` pins in the reader: an empty tuple skips the
    # prefix filter rather than falling back to ``src/``.
    project, _ = indexed_project({"src/app.py": TUPLES, "other/app.py": TUPLES})
    (project / "pyproject.toml").write_text("[tool.pypeeker]\nsrc = []\n")

    paths = [f["path"] for f in _run(project, ("prefer-tuple",))["findings"]]

    assert paths == ["other/app.py", "src/app.py"]
