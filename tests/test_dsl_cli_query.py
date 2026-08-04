"""``pypeeker query``: the read half's consuming surface.

The command is thin by design — it composes a named expression over the symbols
universe and prints the result — but it is where three phase-2 resolutions
become user-visible, so this is where they are pinned by bytes and exit codes:
fork #12's loud evidence-typed anchors (never an empty list for "not found"),
fork #7's versioned ``--why``, and "scope derived from the expression, never
declared" showing up as a reported ``reach``.

Registry note: every invocation here installs ``tuple-candidate`` into the
process-global trait registry and nothing unregisters it. No assertion below
depends on it being absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.cli import main

CLEAN = """\
def f():
    xs = []
    return xs[0]
"""

MUTATED = """\
def g():
    ys = []
    ys.append(1)
    return ys
"""

NOTHING = """\
def h():
    return 1
"""


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create and index a project, leaving the process inside it."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "t"\n')
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    os.chdir(tmp_path)
    result = CliRunner().invoke(main, ["index", "src"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return tmp_path


def _query(*args: str) -> tuple[int, dict]:
    """Run ``pypeeker query`` and return ``(exit_code, parsed JSON)``."""
    result = CliRunner().invoke(main, ["query", *args], catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


@pytest.fixture
def project(tmp_path):
    """One clean candidate (``m:f:xs``) and one mutated non-candidate."""
    return _project(tmp_path, {"src/m.py": CLEAN, "src/g.py": MUTATED})


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


def test_query_reports_the_matching_symbol(project):
    code, payload = _query("tuple-candidate")
    assert code == 0
    assert [r["anchor"]["id"] for r in payload["results"]] == ["m:f:xs"]


def test_the_envelope_names_the_expression_and_the_universe(project):
    _, payload = _query("tuple-candidate")
    assert payload["expression"] == "tuple-candidate"
    assert payload["universe"] == "symbols"


def test_reach_is_derived_and_reported_as_file(project):
    _, payload = _query("tuple-candidate")
    # Nothing in tuple-candidate consults the cross-module resolver, and there
    # is no way for a caller to assert otherwise -- reach is derived.
    assert payload["reach"] == "file"


def test_reported_reach_comes_from_the_expression_not_the_bare_selection(project, monkeypatch):
    """Every builtin today is FILE reach, so "file" passes either way -- pin the
    wiring by handing the command a PROJECT-reach expression and requiring the
    envelope to say so. Reading reach off a selection that has not yet had the
    expression attached would report "file" here."""
    from pypeeker.dsl import opaque

    @opaque("cross-file", reads=("project:resolver",))
    def cross_file(_candidate):
        return True

    monkeypatch.setattr("pypeeker.cli.expression", lambda _name: cross_file)
    _, payload = _query("cross-file")
    assert payload["reach"] == "project"


def test_reported_reach_still_says_file_for_a_file_local_expression(project):
    _, payload = _query("tuple-candidate")
    assert payload["reach"] == "file"


def test_the_anchor_key_is_null_when_no_anchor_was_given(project):
    _, payload = _query("tuple-candidate")
    assert payload["anchor"] is None


def test_each_result_carries_its_anchor_evidence_and_location(project):
    _, payload = _query("tuple-candidate")
    result = payload["results"][0]
    assert result["anchor"] == {
        "kind": "symbol",
        "id": "m:f:xs",
        "evidence": "declared",
    }
    assert result["file_path"] == "src/m.py"
    assert result["line"] == 2


def test_the_match_reports_declared_as_the_divergence_ledger_requires(project):
    _, payload = _query("tuple-candidate")
    # dsl-rewrite.md: "prefer-tuple reports at DECLARED via the meta-read law
    # (fork #4); a port that meets to INFERRED is wrong, not divergent."
    assert payload["results"][0]["confidence"] == "declared"


def test_a_mutated_list_is_not_reported(project):
    _, payload = _query("tuple-candidate")
    assert all(r["anchor"]["id"] != "g:g:ys" for r in payload["results"])


def test_a_project_with_no_candidates_exits_zero_with_an_empty_list(tmp_path):
    _project(tmp_path, {"src/h.py": NOTHING})
    code, payload = _query("tuple-candidate")
    # A resolved query that matched nothing is a true answer, not a failure.
    assert code == 0
    assert payload["results"] == []


# ---------------------------------------------------------------------------
# --why
# ---------------------------------------------------------------------------


def test_why_is_absent_without_the_flag(project):
    _, payload = _query("tuple-candidate")
    assert "why" not in payload["results"][0]


def test_why_adds_a_versioned_derivation_document(project):
    _, payload = _query("tuple-candidate", "--why")
    why = payload["results"][0]["why"]
    assert why["schema"] == 1
    assert why["derivations"][0]["op"] == "all_of"
    assert why["derivations"][0]["confidence"] == "declared"


def test_why_changes_nothing_else_in_the_envelope(project):
    _, without = _query("tuple-candidate")
    _, with_why = _query("tuple-candidate", "--why")
    for result in with_why["results"]:
        result.pop("why")
    assert with_why == without


def test_why_names_the_clause_that_certified_the_confidence(project):
    _, payload = _query("tuple-candidate", "--why")
    nodes = payload["results"][0]["why"]["derivations"][0]["inputs"]
    assertion = next(
        node for node in nodes if node["inputs"] and node["inputs"][0]["op"] == "trait.at"
    )["inputs"][0]
    # The meta-read is what lifts a trait sitting at INFERRED to DECLARED
    # evidence, and --why has to say so or the number is mystifying.
    assert assertion["trait"] == "type-annotation"
    assert assertion["level"] == "inferred"
    assert assertion["meta_read"] is True
    assert assertion["confidence"] == "declared"


def test_why_reports_what_each_node_read(project):
    _, payload = _query("tuple-candidate", "--why")
    assert payload["results"][0]["why"]["derivations"][0]["reads"] == [
        "field:kind",
        "field:scope_kind",
        "trait:type-annotation",
        "trait:variable-mutation",
    ]


def test_why_never_carries_trait_provenance_prose(project):
    result = CliRunner().invoke(main, ["query", "tuple-candidate", "--why"])
    # analysis/traits.py forbids Trait.provenance from reaching output; --why is
    # a different object entirely, and this is where that stops being a claim.
    assert "pypeeker.analysis.type_annotation:" not in result.output
    assert "pypeeker.analysis.variable_mutation:" not in result.output
    assert "recorded type annotation of" not in result.output


# ---------------------------------------------------------------------------
# --anchor
# ---------------------------------------------------------------------------


def test_an_exact_anchor_narrows_to_that_symbol(project):
    code, payload = _query("tuple-candidate", "--anchor", "m:f:xs")
    assert code == 0
    assert [r["anchor"]["id"] for r in payload["results"]] == ["m:f:xs"]
    assert payload["anchor"] == "m:f:xs"


def test_a_cli_typed_anchor_carries_declared_evidence(project):
    _, payload = _query("tuple-candidate", "--anchor", "m:f:xs")
    # Fork #12: a CLI-typed symbol id is DECLARED evidence.
    assert payload["results"][0]["anchor"]["evidence"] == "declared"


def test_an_anchor_that_resolves_but_does_not_match_exits_zero(project):
    code, payload = _query("tuple-candidate", "--anchor", "g:g:ys")
    assert code == 0
    assert payload["results"] == []


def test_an_unresolved_anchor_is_refused_with_candidates(project):
    code, payload = _query("tuple-candidate", "--anchor", "src/m.py:f.xs")
    assert code == 1
    assert payload["code"] == "unresolved-anchor"
    assert payload["anchor"] == "src/m.py:f.xs"
    assert payload["candidates"] == ["m:f:xs"]


def test_an_unresolved_anchor_with_no_near_match_still_refuses(project):
    code, payload = _query("tuple-candidate", "--anchor", "totally:unrelated")
    # Never an empty result set: "nothing matched your anchor" and "your
    # predicate selected nothing" are different answers (fork #12).
    assert code == 1
    assert payload["code"] == "unresolved-anchor"
    assert payload["candidates"] == []
    assert "results" not in payload


def test_an_ambiguous_anchor_is_refused_listing_every_candidate(tmp_path):
    _project(tmp_path, {"src/a.py": CLEAN, "src/b.py": CLEAN})
    code, payload = _query("tuple-candidate", "--anchor", "xs")
    assert code == 1
    assert payload["code"] == "ambiguous-anchor"
    assert payload["candidates"] == ["a:f:xs", "b:f:xs"]
    assert "Pass one of these ids instead" in payload["error"]


def test_the_anchor_clause_appears_in_why_like_any_other(project):
    _, payload = _query("tuple-candidate", "--anchor", "m:f:xs", "--why")
    derivations = payload["results"][0]["why"]["derivations"]
    # Two written clauses, in written order: the anchor restriction, then the
    # expression. There is no optimizer to fold or reorder them.
    assert len(derivations) == 2
    assert derivations[0]["reads"] == ["field:symbol_id"]
    assert derivations[1]["op"] == "all_of"


# ---------------------------------------------------------------------------
# unknown expression
# ---------------------------------------------------------------------------


def test_an_unknown_expression_is_refused_by_listing_the_known_ones(project):
    code, payload = _query("no-such-expression")
    assert code == 1
    assert payload["code"] == "unknown-expression"
    assert payload["expression"] == "no-such-expression"
    assert payload["valid"] == ["tuple-candidate"]


def test_the_unknown_expression_refusal_reads_as_a_remedy(project):
    _, payload = _query("no-such-expression")
    assert payload["error"] == (
        "unknown expression 'no-such-expression'; registered expressions are: "
        "tuple-candidate"
    )


def test_an_unknown_expression_is_checked_before_the_anchor(project):
    # The positional argument is the cheaper and more likely mistake; reporting
    # the anchor first would send a caller chasing the wrong error.
    _, payload = _query("no-such-expression", "--anchor", "also:bogus")
    assert payload["code"] == "unknown-expression"


# ---------------------------------------------------------------------------
# discoverability
# ---------------------------------------------------------------------------


def test_query_is_listed_in_the_top_level_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "query" in result.output


def test_the_command_help_documents_its_refusal_codes():
    result = CliRunner().invoke(main, ["query", "--help"])
    assert result.exit_code == 0
    for code in ("unknown-expression", "unresolved-anchor", "ambiguous-anchor"):
        assert code in result.output


def test_the_command_help_documents_the_flags():
    result = CliRunner().invoke(main, ["query", "--help"])
    assert "--anchor" in result.output
    assert "--why" in result.output
    assert "--no-refresh" in result.output
