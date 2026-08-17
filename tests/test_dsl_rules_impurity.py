"""The impurity pair on the DSL: the option branches and the traps the oracle cannot see.

``scripts/differential-check.py`` grades both rules against the frozen engine on
``tests/fixtures/parity/impurity``, and that corpus is where the message shapes,
the confidence tiers and the ``allow`` predicates are proved. A corpus carries
**one** configuration, though, so three things stay ungraded there and are
asserted here instead.

The first is the option semantics that need a *second* configuration to be
visible: the frozen ``_contract_for`` matches a decorator by its head **or** by
the head's last dotted segment, so a configured ``functools.cache`` matches only
the prefixed spelling while a configured ``cache`` covers both. The parity
corpus configures supersets of the defaults, which cannot show that asymmetry.

The second is the decorator-beats-dunder rule seen as a *count*: the oracle
compares finding sets, so it would pass just as happily if the port reported an
``@property``-decorated ``__len__`` under the dunder contract as well and the
frozen engine happened to sort the same way. "Exactly one finding" is the claim.

The third is the params trap, and it is the reason this file exists at all.
Neither rule's ``allow`` option may reach
:meth:`pypeeker.analysis.purity.PurityPolicy.extended`'s ``allow=`` parameter:
``pure-decorator-contracts`` passes **no policy** to ``impurities()`` and
``import-time-side-effects``' ``_configured_policy`` reads ``extra-impure``
only. Both ``allow`` lists are fnmatch patterns over *ids*, and folding them
into the policy would subtract those names from every denylist and make
unrelated functions look pure. Nothing in the type system catches it: passing
``purity_params(options)`` at either call site type-checks, and on the parity
corpus it would still produce the same 22 findings, because no pattern there
happens to spell a denylisted name. These tests choose patterns that do.
"""

import pytest

from pypeeker.dsl import Corpus, dsl_rule
from pypeeker.models import Confidence

CONTRACTS = "pure-decorator-contracts"
IMPORT_TIME = "import-time-side-effects"


# ---------------------------------------------------------------------------
# pure-decorator-contracts: the decorator/dunder option branches
# ---------------------------------------------------------------------------

# `bare` and `dotted` are the same impurity under the same decorator written two
# ways. `Cmp.__eq__` and `Cmp.__bool__` are the same for the dunder list.
CONTRACT_FILES = {
    "app/__init__.py": "",
    "app/deco.py": '''\
"""Two spellings of one decorator."""
import functools
from functools import cache


@cache
def bare(path):
    """Impure under a bare `@cache`."""
    return open(path)


@functools.cache
def dotted(path):
    """Impure under a dotted `@functools.cache`."""
    return open(path)
''',
    "app/dunder.py": '''\
"""Two dunders, one impure each."""


class Cmp:
    """Holder."""

    def __eq__(self, other):
        """Impure comparison."""
        return open("a") == other

    def __bool__(self):
        """Impure truthiness."""
        return bool(open("a"))
''',
    "app/both.py": '''\
"""A dunder that also carries a decorator contract."""


class Sized:
    """Holder."""

    @property
    def __len__(self):
        """Under BOTH contracts — reported once, as the decorator."""
        return len(open("a").read())
''',
}


@pytest.fixture
def contract_corpus(indexed_project):
    """A package whose functions are impure under decorator and dunder contracts."""
    _, store = indexed_project(CONTRACT_FILES)
    return Corpus(store, ("app",))


def _messages(rule: str, options, corpus) -> list[str]:
    return [f.message for f in dsl_rule(rule).findings(options, corpus)]


def test_a_dotted_configured_decorator_matches_only_the_dotted_spelling(contract_corpus):
    # The frozen `_contract_for` tests `head in decorators` OR
    # `head.rsplit(".", 1)[-1] in decorators`. A configured `functools.cache`
    # can only satisfy the first, so the bare `@cache` spelling goes silent.
    # The dunder list is disabled so the decorator arm is the only thing under
    # test; an empty list would fall back to the defaults, which is the frozen
    # `_as_str_list(...) or DEFAULT_DUNDERS`.
    options = {"decorators": ["functools.cache"], "dunders": ["__nothing__"]}
    assert _messages(CONTRACTS, options, contract_corpus) == [
        "function 'app.deco:dotted' violates the @functools.cache purity "
        "contract: BareCall 'open' (line 15)"
    ]


def test_a_bare_configured_decorator_matches_both_spellings(contract_corpus):
    # The mirror image, and the reason the asymmetry is real rather than a
    # normalization bug: the last-segment arm makes `cache` cover both, and the
    # message still quotes each head AS WRITTEN.
    options = {"decorators": ["cache"], "dunders": ["__nothing__"]}
    assert _messages(CONTRACTS, options, contract_corpus) == [
        "function 'app.deco:bare' violates the @cache purity contract: "
        "BareCall 'open' (line 9)",
        "function 'app.deco:dotted' violates the @functools.cache purity "
        "contract: BareCall 'open' (line 15)",
    ]


def test_a_configured_dunder_list_replaces_the_default_one(contract_corpus):
    # `__eq__` is a default contract dunder and `__bool__` is not; configuring
    # `__bool__` alone must swap them, not add to them. The contract name has no
    # `@` prefix — that is the frozen `f"{symbol.name}"`.
    messages = _messages(
        CONTRACTS, {"decorators": ["nothing"], "dunders": ["__bool__"]}, contract_corpus
    )
    assert messages == [
        "method 'app.dunder:Cmp.__bool__' violates the __bool__ purity "
        "contract: BareCall 'open' (line 13)"
    ]
    assert "__eq__" not in "".join(messages)


def test_a_decorated_dunder_is_reported_once_under_the_decorator(contract_corpus):
    # Both contracts apply to `Sized.__len__`. The frozen loop returns on the
    # first decorator match, so the dunder arm is never reached: one finding,
    # worded `@property`. A port that tested the two contracts independently
    # would report two, and the differential oracle compares SETS — it would see
    # the extra one, but only if a corpus happened to carry this shape.
    findings = dsl_rule(CONTRACTS).findings({}, contract_corpus)
    both = [f for f in findings if f.path == "app/both.py"]
    assert len(both) == 1
    assert both[0].message == (
        "method 'app.both:Sized.__len__' violates the @property purity "
        "contract: BareCall 'open' (line 10); AttributeMethodCall 'read' (line 10)"
    )
    assert both[0].confidence is Confidence.DECLARED


def test_the_allow_list_is_an_id_glob_and_never_reaches_the_purity_policy(
    contract_corpus,
):
    # `open` is a denylisted builtin name AND a legal fnmatch pattern. As an id
    # glob it matches no symbol id here, so every finding stands. Were the
    # rule's options handed to `purity_params`, `PurityPolicy.extended(allow=
    # ["open"])` would drop `open` from the builtin denylist and EVERY finding
    # in this corpus would vanish — silently, and project-wide.
    with_allow = _messages(CONTRACTS, {"allow": ["open"]}, contract_corpus)
    assert with_allow == _messages(CONTRACTS, {}, contract_corpus)
    assert len(with_allow) == 4


def test_the_allow_list_still_silences_a_matching_id(contract_corpus):
    # The other half of the same claim: `allow` is applied, just as an id glob.
    messages = _messages(CONTRACTS, {"allow": ["app.deco:*"]}, contract_corpus)
    assert [m.split("'")[1] for m in messages] == [
        "app.both:Sized.__len__",
        "app.dunder:Cmp.__eq__",
    ]


# ---------------------------------------------------------------------------
# import-time-side-effects: the same trap on the other rule
# ---------------------------------------------------------------------------

# `helper` is impure only because of the builtin it calls, and `run()` calls it
# at import time — so the finding exists only if the POLICY still denies `open`.
IMPORT_TIME_FILES = {
    "svc/__init__.py": "",
    "svc/work.py": '''\
"""The impure project function."""


def helper(path):
    """Impure through a denylisted builtin."""
    return open(path)
''',
    "svc/boot.py": '''\
"""Calls it while being imported."""
from svc.work import helper

HANDLE = helper("a")
''',
}


@pytest.fixture
def import_time_corpus(indexed_project):
    """A package that calls an impure project function at import time."""
    _, store = indexed_project(IMPORT_TIME_FILES)
    return Corpus(store, ("svc",))


def test_the_project_arm_fires_on_a_transitively_impure_import_time_call(
    import_time_corpus,
):
    findings = dsl_rule(IMPORT_TIME).findings({}, import_time_corpus)
    assert [(f.path, f.line, f.message, f.confidence) for f in findings] == [
        (
            "svc/boot.py",
            4,
            "import-time call to 'svc.work:helper' resolves to an impure "
            "project function",
            Confidence.DECLARED,
        )
    ]


def test_this_rules_allow_list_never_reaches_the_purity_policy_either(
    import_time_corpus,
):
    # `_configured_policy` reads `extra-impure` and nothing else, on purpose.
    # `open` as an allow pattern matches neither the reported name
    # (`svc.work:helper`) nor the calling module (`svc.boot`), so the finding
    # stands. Folding it into the policy would make `helper` look pure and take
    # the finding with it — the one shape where the two are distinguishable,
    # because shape 3's verdict comes from the transitive walk rather than from
    # the call site's own name.
    assert dsl_rule(IMPORT_TIME).findings({"allow": ["open"]}, import_time_corpus) == (
        dsl_rule(IMPORT_TIME).findings({}, import_time_corpus)
    )


def test_the_allow_list_still_silences_the_resolved_project_id(import_time_corpus):
    # And is applied — against the name shape 3 reports, which is the canonical
    # symbol id and not the local alias the call site spells.
    assert dsl_rule(IMPORT_TIME).findings({"allow": ["*:helper"]}, import_time_corpus) == []


TIER_FILES = {
    "tier/__init__.py": "",
    "tier/mod.py": '''\
"""One resolved builtin and one free name, both called at import time."""

print("resolved")
log("free")
''',
}


@pytest.fixture
def tier_corpus(indexed_project):
    """Two import-time bare calls the binder resolves differently."""
    _, store = indexed_project(TIER_FILES)
    return Corpus(store, ("tier",))


def test_the_builtin_arm_reports_a_resolved_builtin_and_a_free_name_at_different_tiers(
    tier_corpus,
):
    # The frozen tier split, and the reason this rule does not spell it as a
    # `weakened_when`: `Weaken` is inventoried to the dynamic-access family (see
    # `pypeeker.dsl.impurity._builtin_match_evidence`), so the levels ride on
    # the lattice instead. Whatever carries them, the observable claim is this:
    # both rows survive, and only their labels differ.
    findings = dsl_rule(IMPORT_TIME).findings({"extra-impure": ["log"]}, tier_corpus)
    assert [(f.line, f.message, f.confidence) for f in findings] == [
        (
            3,
            "import-time call to 'print' matches the impure-builtin policy",
            Confidence.DECLARED,
        ),
        (
            4,
            "import-time call to 'log' matches the impure-builtin policy",
            Confidence.HEURISTIC,
        ),
    ]


def test_the_allow_list_also_matches_the_calling_module_path(import_time_corpus):
    # The frozen `_allowed` fnmatches each pattern against the call name AND
    # against `_module_path(index)` — the file's MODULE SCOPE id, published as
    # `row.module_scope_id`.
    assert dsl_rule(IMPORT_TIME).findings({"allow": ["svc.boot"]}, import_time_corpus) == []
