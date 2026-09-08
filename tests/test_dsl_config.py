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

import pytest

from pypeeker.dsl import PROJECT_VISIBILITY_KEY
from pypeeker.dsl import read_config as _read_config
from pypeeker.dsl.config import DEFAULT_SRC
from pypeeker.project import DEFAULT_SRC_ROOTS, ConfigOptionError


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
    # under PROJECT_VISIBILITY_KEY. A reader that skips this under-reports on
    # exactly the projects that declare the section — including this
    # repository. The key is NOT "visibility": that is also the name of
    # require-docstrings' own enum option, and the collision emptied that
    # option's set in silence (TASK-163).
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings", "prefer-tuple"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'allow-decorators = ["public_api"]\n'
    )
    _src, _rules, _plugins, options = _read_config(tmp_path)
    assert options == {
        "require-docstrings": {
            PROJECT_VISIBILITY_KEY: {"allow-decorators": ["public_api"]}
        },
        "prefer-tuple": {PROJECT_VISIBILITY_KEY: {"allow-decorators": ["public_api"]}},
    }


def test_a_rule_option_named_visibility_no_longer_collides_with_the_injection(tmp_path):
    # Was test_an_explicit_rule_option_wins_over_the_injected_visibility_table,
    # whose premise — one key, two meanings, last writer wins — is gone. The
    # rule's own `visibility` option and the project-wide table now occupy
    # different keys, so both survive intact and the rule reads the one it
    # asked for.
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
    assert options["require-docstrings"][PROJECT_VISIBILITY_KEY] == {
        "allow-decorators": ["public_api"]
    }


def test_a_rule_table_declaring_the_reserved_injection_key_refuses(tmp_path):
    # The reserved key cannot be shadowed: a rule table writing it would make
    # the injection a no-op and hand that rule a table the project never
    # declared.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings"]\n'
        "\n"
        "[tool.pypeeker.require-docstrings]\n"
        f'{PROJECT_VISIBILITY_KEY} = ["nope"]\n'
    )
    with pytest.raises(ConfigOptionError) as exc:
        _read_config(tmp_path)
    assert PROJECT_VISIBILITY_KEY in str(exc.value)
    assert "reserved" in str(exc.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [("rules", "prefer-tuple"), ("plugins", "lint_rules"), ("src", "pkg")],
)
def test_a_bare_string_top_level_list_key_refuses_instead_of_splitting(
    tmp_path, key, value
):
    # These three are lists by contract, and `tuple("prefer-tuple")` iterated
    # them per character: `rules` refused with `unknown expression 'p'`,
    # `plugins` tracebacked on module 'l', and `src` matched no file and
    # exited 0. The message names the key and shows the bracketed form.
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.pypeeker]\n{key} = {value!r}\n"
    )
    with pytest.raises(ConfigOptionError) as exc:
        _read_config(tmp_path)
    message = str(exc.value)
    assert f"option {key!r}" in message
    assert f"{key} = [{value!r}]" in message


def test_a_visibility_table_key_typo_refuses_instead_of_being_inert(tmp_path):
    # `public_roots` (underscore) parses as TOML, is never read, and leaves a
    # library-mode project protecting nothing — silently, until now.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pypeeker]\n"
        'rules = ["require-docstrings"]\n'
        "\n"
        "[tool.pypeeker.visibility]\n"
        'public_roots = ["pkg"]\n'
    )
    with pytest.raises(ConfigOptionError) as exc:
        _read_config(tmp_path)
    message = str(exc.value)
    assert "visibility.public_roots" in message
    assert "public-roots" in message


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
