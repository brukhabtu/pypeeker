"""The shipped ``[tool.pypeeker]`` reader: what a rule run can observe about a project.

:func:`pypeeker.dsl.read_config` is the one config reader behind every consumer
of the engine — ``pypeeker check`` (via :func:`pypeeker.app.run_check`),
``pypeeker privatize`` (via :func:`pypeeker.app.run_privatize`) and a
``batch`` file's ``fix`` entry (via ``pypeeker.app.batch_intents``). Its two
subtle behaviours are pinned here: the source-root default fires only when the
key is *absent*, and the project-wide ``[tool.pypeeker.visibility]`` table is
injected into every enabled rule's options.

These scenarios moved verbatim from ``tests/test_dsl_differential_runner.py``
when the differential oracle was retired; the last two came from
``tests/test_check_config.py`` when the frozen reader was deleted at the flip.
Together they are the only coverage the reader has.
"""

from pypeeker.dsl import read_config as _read_config
from pypeeker.dsl.config import DEFAULT_SRC
from pypeeker.project import DEFAULT_SRC_ROOTS


def test_a_target_without_a_pyproject_falls_back_to_the_default_src_root(tmp_path):
    assert _read_config(tmp_path) == (("src",), (), (), {})


def test_a_pyproject_without_a_pypeeker_section_falls_back_too(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert _read_config(tmp_path) == (("src",), (), (), {})


def test_src_roots_come_from_the_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pypeeker]\nsrc = ["lib", "app"]\n')
    src, _rules, _plugins, _options = _read_config(tmp_path)
    assert src == ("lib", "app")


def test_an_explicit_empty_src_list_is_not_the_default_root(tmp_path):
    # The default fires only when the key is ABSENT
    # (section.get("src", list(DEFAULT_SRC))), and a corpus built from an empty
    # tuple skips the prefix filter entirely. Coercing [] to ("src",) here
    # would silently under-report on any project that writes src = [].
    (tmp_path / "pyproject.toml").write_text("[tool.pypeeker]\nsrc = []\n")
    src, _rules, _plugins, _options = _read_config(tmp_path)
    assert src == ()


def test_rule_subtables_become_option_tables_and_reserved_keys_do_not(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        'rules = ["require-docstrings"]\n'
        "plugins = []\n"
        "\n"
        "[tool.pypeeker.require-docstrings]\n"
        'kinds = ["function"]\n'
    )
    _src, _rules, _plugins, options = _read_config(tmp_path)
    assert options == {"require-docstrings": {"kinds": ["function"]}}


def test_the_project_wide_visibility_table_is_injected_into_every_enabled_rule(tmp_path):
    # [tool.pypeeker.visibility] is copied into each enabled rule's options
    # under the reserved key "visibility". A reader that skips this
    # under-reports on exactly the projects that declare the section —
    # including this repository.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings", "prefer-tuple"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'allow-decorators = ["public_api"]\n'
    )
    _src, _rules, _plugins, options = _read_config(tmp_path)
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
    _src, _rules, _plugins, options = _read_config(tmp_path)
    assert options["require-docstrings"]["visibility"] == ["private"]


def test_a_rule_that_is_not_enabled_gets_no_injected_options(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["prefer-tuple"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'allow-decorators = ["public_api"]\n'
    )
    _src, _rules, _plugins, options = _read_config(tmp_path)
    assert set(options) == {"prefer-tuple"}


def test_the_default_src_root_is_shared_with_the_project_module(tmp_path):
    """One source of truth for "where does code live" (from ``test_check_config.py``).

    ``pypeeker.project`` owns the ``[tool.pypeeker]`` table, and the reader's
    default must be that module's constant rather than a second literal that
    can drift away from it.
    """
    assert DEFAULT_SRC is DEFAULT_SRC_ROOTS


def test_the_enabled_rule_list_comes_back_as_a_tuple(tmp_path):
    """The ``rules`` slot, from ``test_check_config.py``'s ``test_parses_rules_and_src``.

    Every other scenario here unpacks ``rules`` into a throwaway, so without
    this one a reader that dropped the key entirely would still pass.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'src = ["src", "tests"]\n'
        'rules = ["require-docstrings"]\n'
    )
    src, rules, _plugins, _options = _read_config(tmp_path)
    assert src == ("src", "tests")
    assert rules == ("require-docstrings",)
