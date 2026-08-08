"""``no-impure-functions`` on the DSL: what is in scope, what is impure, how sure.

The differential oracle grades this rule against the frozen engine on
``tests/fixtures/parity/purity``. These tests pin the half the oracle cannot:
pypeeker's own configuration has no ``[tool.pypeeker.no-impure-functions]``
table at all, so on the ``self`` target the rule is a **no-op by design** and
compares 0-vs-0.

Two things here are easy to port wrong and are asserted directly. The first is
the confidence tier: an impurity resting entirely on observations whose receiver
the binder could not classify is ``HEURISTIC``, and one with any structurally
grounded observation is ``DECLARED``. The second is the "no ``include`` means
no-op" law — the old rule returns before looking at anything, which is a
deliberate refusal to run a heuristic analysis project-wide rather than an
accident of an empty pattern list.
"""

import pytest

from pypeeker.dsl import Corpus, DslRule, Finding, Reach, dsl_rule
from pypeeker.models import Confidence

RULE = "no-impure-functions"

# `writes` calls a builtin the default policy denies. `caller` is pure by
# inspection and impure by propagation. `many` overflows the three-observation
# summary. `clean` is pure. `logs` is impure only under `extra-impure`, and
# `prints` only until `print` is allowed. `skipped` exists to be excluded.
PURE_FILES = {
    "pure/__init__.py": "",
    "pure/io.py": '''\
"""IO."""


def writes(path):
    """Impure."""
    return open(path)
''',
    "pure/caller.py": '''\
"""Caller."""
from pure.io import writes


def caller(path):
    """Transitively impure."""
    return writes(path)
''',
    "pure/clean.py": '''\
"""Clean."""


def clean(a, b):
    """Pure."""
    return a + b
''',
    "pure/many.py": '''\
"""Many."""


def many(path):
    """Four impurities, so the summary truncates."""
    open(path)
    open(path)
    open(path)
    open(path)
    return None
''',
    "pure/extra.py": '''\
"""Extra."""


def logs(msg):
    """Impure only when 'log' joins the builtin denylist."""
    log(msg)
    return None
''',
    "pure/allowed.py": '''\
"""Allowed."""


def prints(msg):
    """Impure until 'print' is allowed."""
    print(msg)
    return None
''',
    "pure/skipped.py": '''\
"""Skipped."""


def skipped(path):
    """Excluded from the rule's scope."""
    return open(path)
''',
    "pure/weak_cases.py": '''\
"""Cases whose receiver the binder classifies differently."""


def via_call_result(thing):
    """Receiver is a call result — unclassifiable."""
    thing.get().write("a")
    return None


def via_param(handle):
    """Receiver is a parameter — structurally grounded."""
    handle.write("a")
    return None
''',
}

OPTIONS = {
    "include": ["pure.*"],
    "exclude": ["pure.skipped"],
    "extra-impure": ["log"],
    "allow": ["print"],
}


def _finding(path: str, line: int, message: str, confidence=Confidence.DECLARED):
    return Finding(rule=RULE, path=path, line=line, message=message, confidence=confidence)


WRITES = _finding(
    "pure/io.py", 4, "function 'pure.io:writes' is impure: BareCall 'open' (line 6)"
)
CALLER = _finding(
    "pure/caller.py",
    5,
    "function 'pure.caller:caller' is impure: TransitiveImpureCall 'pure.io:writes'",
)
MANY = _finding(
    "pure/many.py",
    4,
    "function 'pure.many:many' is impure: BareCall 'open' (line 6); "
    "BareCall 'open' (line 7); BareCall 'open' (line 8); +1 more",
)
LOGS = _finding(
    "pure/extra.py", 4, "function 'pure.extra:logs' is impure: BareCall 'log' (line 6)"
)
VIA_CALL_RESULT = _finding(
    "pure/weak_cases.py",
    4,
    "function 'pure.weak_cases:via_call_result' is impure: "
    "AttributeMethodCall 'write' (line 6)",
    Confidence.HEURISTIC,
)
VIA_PARAM = _finding(
    "pure/weak_cases.py",
    10,
    "function 'pure.weak_cases:via_param' is impure: AttributeMethodCall 'write' (line 12)",
)


def _rule() -> DslRule:
    rule = dsl_rule(RULE)
    assert isinstance(rule, DslRule)
    return rule


def _findings(corpus: Corpus, **overrides) -> list[Finding]:
    return _rule().findings({**OPTIONS, **overrides}, corpus)


@pytest.fixture
def purity_corpus(indexed_project):
    """The project above, indexed under ``pure/``.

    Paths are rooted at the package rather than under a ``src/`` prefix: the
    binder derives module ids from the indexed path verbatim, and the
    ``include = ["pure.*"]`` patterns have to match them. ``pypeeker index src``
    strips the configured source root, which is why the on-disk parity fixture
    keeps its ``src/`` layer.
    """
    _, store = indexed_project(PURE_FILES)
    return Corpus(store, ("pure",))


# ---------------------------------------------------------------------------
# the whole rule
# ---------------------------------------------------------------------------


def test_the_rule_reports_every_impure_in_scope_function_in_index_order(purity_corpus):
    assert _findings(purity_corpus) == [
        CALLER,
        LOGS,
        WRITES,
        MANY,
        VIA_CALL_RESULT,
        VIA_PARAM,
    ]


# ---------------------------------------------------------------------------
# wording
# ---------------------------------------------------------------------------


def test_the_summary_shows_three_observations_and_counts_the_rest(purity_corpus):
    # Four `open` calls; the message names the first three with 1-indexed lines
    # and closes with "+1 more".
    assert MANY in _findings(purity_corpus)


def test_a_transitive_impurity_is_worded_without_a_line_suffix(purity_corpus):
    # A TransitiveImpureCall is about a callee, not about a site, so it carries
    # no `line` attribute and the "(line N)" suffix is omitted. `caller` itself
    # calls nothing impure directly.
    assert CALLER in _findings(purity_corpus)
    assert "(line" not in CALLER.message


# ---------------------------------------------------------------------------
# confidence — the one sweep that declares its own tier
# ---------------------------------------------------------------------------


def test_an_unclassifiable_receiver_reports_at_heuristic(purity_corpus):
    reported = {f.line: f for f in _findings(purity_corpus) if f.path == "pure/weak_cases.py"}
    assert reported[4].confidence is Confidence.HEURISTIC


def test_a_parameter_receiver_keeps_the_verdict_declared(purity_corpus):
    # Same observation type, same method name, same message shape — only the
    # receiver differs, and that is the whole of the tier decision.
    reported = {f.line: f for f in _findings(purity_corpus) if f.path == "pure/weak_cases.py"}
    assert reported[10].confidence is Confidence.DECLARED


# ---------------------------------------------------------------------------
# scoping
# ---------------------------------------------------------------------------


def test_no_include_patterns_switches_the_rule_off_entirely(purity_corpus):
    assert _rule().build({**OPTIONS, "include": []}) is None
    assert _findings(purity_corpus, include=[]) == []


def test_an_absent_include_key_switches_the_rule_off_too(purity_corpus):
    assert _rule().build({}) is None
    assert _rule().findings({}, purity_corpus) == []


def test_include_scopes_the_rule_to_matching_modules(purity_corpus):
    assert _findings(purity_corpus, include=["pure.io"], exclude=[]) == [WRITES]


def test_include_also_matches_a_full_symbol_id(purity_corpus):
    assert _findings(purity_corpus, include=["pure.io:writes"], exclude=[]) == [WRITES]


def test_exclude_wins_over_include(purity_corpus):
    reported = _findings(purity_corpus)
    assert not [f for f in reported if f.path == "pure/skipped.py"]
    # …and dropping the exclusion brings it back, so the silence is the option
    # and not an accident of the corpus.
    assert "pure/skipped.py" in {f.path for f in _findings(purity_corpus, exclude=[])}


def test_a_pure_function_in_scope_is_not_reported(purity_corpus):
    assert not [f for f in _findings(purity_corpus) if f.path == "pure/clean.py"]


# `paths.module_path_from` returns "" for the __init__.py of a source root that
# is itself a package, so the binder emits no MODULE symbol and every symbol in
# the file has an id like ":rooty". The frozen rule matches `include` against
# the symbol id and against `symbol_id.split(":", 1)[0]` — "" here.
ROOT_PACKAGE_FILES = {
    "__init__.py": '''\
"""A source root that is itself a package."""


def rooty(path):
    """Impure."""
    return open(path)
''',
}


@pytest.fixture
def root_package_corpus(indexed_project):
    _, store = indexed_project(ROOT_PACKAGE_FILES)
    return Corpus(store, ())


def test_a_pattern_matching_the_file_path_does_not_pull_in_a_rootless_module(
    root_package_corpus,
):
    # Guard: this only proves anything while the symbol really has no module
    # segment in its id.
    assert _symbol_ids(root_package_corpus) == [":rooty"]
    # `__init__*` matches the file path "__init__.py" and matches neither the
    # symbol id nor its (empty) module segment. The frozen engine reports
    # nothing here — verified end to end against `pypeeker check --strict` on a
    # materialized `src = ["src"]` tree with a `src/__init__.py`. Matching the
    # patterns against the row's `module` field, which falls back to the file
    # path, flagged it.
    assert _findings(root_package_corpus, include=["__init__*"], exclude=[]) == []


def test_the_empty_pattern_selects_the_rootless_module_as_the_frozen_rule_does(
    root_package_corpus,
):
    # The mirror image, so the fix cannot be "never match the module segment":
    # `""` matches the empty module segment and fires, at the symbol id the
    # frozen engine quotes verbatim.
    assert _findings(root_package_corpus, include=[""], exclude=[]) == [
        _finding("__init__.py", 4, "function ':rooty' is impure: BareCall 'open' (line 6)")
    ]


def _symbol_ids(corpus: Corpus) -> list[str]:
    """Every function symbol id in the corpus."""
    from pypeeker.models import SymbolKind

    return [
        s.symbol_id
        for index in corpus.indexes
        for s in index.symbols
        if s.kind is SymbolKind.FUNCTION
    ]


# ---------------------------------------------------------------------------
# policy options
# ---------------------------------------------------------------------------


def test_extra_impure_adds_a_bare_name_to_the_builtin_denylist(purity_corpus):
    assert LOGS in _findings(purity_corpus)
    assert LOGS not in _findings(purity_corpus, **{"extra-impure": []})


def test_allow_removes_a_name_from_every_denylist(purity_corpus):
    assert not [f for f in _findings(purity_corpus) if f.path == "pure/allowed.py"]
    assert "pure/allowed.py" in {f.path for f in _findings(purity_corpus, allow=[])}


# ---------------------------------------------------------------------------
# shape and cost
# ---------------------------------------------------------------------------


def test_the_rule_leaves_the_file_because_purity_propagates_across_modules():
    selection = _rule().build(OPTIONS)
    assert selection is not None
    assert selection.stages == ("where", "where", "where", "with_field:impurities")
    assert selection.reach is Reach.PROJECT


def test_without_an_exclude_option_the_rule_has_one_fewer_clause():
    selection = _rule().build({"include": ["pure.*"]})
    assert selection is not None
    assert selection.stages == ("where", "where", "with_field:impurities")


def test_the_call_graph_walk_is_paid_only_for_rows_the_cheap_clauses_accepted(
    purity_corpus, monkeypatch
):
    # The fact table is lazy precisely so the kind / include / exclude filters
    # run first, matching the old rule, whose `continue` statements sit above
    # its `impurities(...)` call. Narrowing include to one module must narrow
    # the walk to the functions in it.
    from pypeeker.dsl import sweeps

    asked: list[str] = []
    real = sweeps.impurities

    def counting(store, symbol_id, **kwargs):
        asked.append(symbol_id)
        return real(store, symbol_id, **kwargs)

    monkeypatch.setattr(sweeps, "impurities", counting)
    _findings(purity_corpus, include=["pure.io"], exclude=[])
    assert asked == ["pure.io:writes"]
