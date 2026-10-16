"""The mutation pair (phase 3e): no-argument-mutation and no-hidden-global-mutation.

This repository (150 and 5 findings) and the retired ``mutation`` parity corpus
(10 and 9) exercised the majority path and every configured option over real
files; the corpus went with the differential oracle at the flip (TASK-157). This
file was always the other half — the things a multiset of findings cannot show:

* that the **enclosing-function walk** is a walk and not a guess: it crosses
  class and comprehension scopes, stops at a nested def, and answers *nothing*
  inside a lambda body;
* that the two leaf-name helpers are **deliberately different functions**, so a
  later simplification collapsing them fails here rather than in a corpus that
  happens not to contain the input that separates them;
* that the module-scope test answers ``False`` for a missing parent scope and
  ``True`` for the empty one — the module-less-file shape, which no fixture
  corpus can carry without becoming the place that edge first gets graded;
* where each part puts the enclosing-function clause and the ``allow`` clause:
  the argument parts **open with** them (the frozen outer loop's place), the
  three global reference parts **close with** them, after their own selecting
  clauses, so the allow patterns are tested only against rows the part could
  report (TASK-0113);
* that the parts partition the rows rather than overlapping, and that the
  global rule's four shapes are reported the same whatever the ``allow`` list.
"""

from dataclasses import dataclass
from typing import Any

import pytest

import pypeeker.dsl.expr

from pypeeker.dsl import (
    Corpus,
    allow_by_id,
    allow_by_id_or_module,
    argument_attribute_write,
    argument_mutator_call,
    argument_subscript_write,
    dsl_rule,
    global_import_attribute_write,
    global_mutator_call,
    global_outer_scope_write,
    global_rebind,
    not_,
    row,
)
from pypeeker.dsl.mutation_rules import _at_module_scope, _leaf, _leaf_method
from pypeeker.dsl.selection import _Where

ARGUMENT = "no-argument-mutation"
GLOBAL = "no-hidden-global-mutation"

ARGUMENT_PARTS = (
    argument_mutator_call,
    argument_attribute_write,
    argument_subscript_write,
)
GLOBAL_REFERENCE_PARTS = (
    global_outer_scope_write,
    global_mutator_call,
    global_import_attribute_write,
)


@pytest.fixture
def corpus_of(indexed_project):
    """Build a corpus over ``files`` with no source-root filter.

    Paths carry no ``src/`` prefix, for the reason
    ``tests/test_dsl_crossfile_rules.py`` gives: the binder derives a module id
    from the indexed path, so ``owner.py`` is the module ``owner``.
    """

    def _build(files: dict[str, str]) -> Corpus:
        _, store = indexed_project(files)
        return Corpus(store, ())

    return _build


def _messages(rule_id: str, corpus: Corpus, options: dict | None = None) -> list[str]:
    return [f.message for f in dsl_rule(rule_id).findings(options or {}, corpus)]


@dataclass
class _Row:
    """A stand-in row for calling an opaque's body directly.

    The bodies under test read one or two fields and nothing else — that is what
    their ``reads=`` declaration says — so a stub with those attributes is the
    whole of their input.
    """

    symbol_id: str = ""
    is_attribute_access: bool = False
    binding_parent_scope_id: Any = None


# ---------------------------------------------------------------------------
# the enclosing-function walk
# ---------------------------------------------------------------------------

LAMBDA_BODY = '''"""A mutation inside a lambda body has no function to charge."""


def make(sink: list) -> object:
    """Return a closure that mutates the captured parameter."""
    return lambda value: sink.append(value)


def direct(sink: list) -> None:
    """The same mutation, written in a function body, must fire."""
    sink.append(1)
'''


def test_a_mutation_inside_a_lambda_body_charges_no_function(corpus_of):
    """The negative that pins the walk.

    A lambda scope owns no FUNCTION/METHOD symbol, so no frozen
    ``AnalysisContext`` subtree contains the reference — the frozen loop stops
    descending at exactly that boundary. A port reading "innermost enclosing
    scope" as "innermost enclosing function" fires here where the frozen engine
    is silent, and every other shape in this file would still pass.
    """
    corpus = corpus_of({"lam.py": LAMBDA_BODY})
    assert _messages(ARGUMENT, corpus) == [
        "function 'lam:direct': parameter 'sink' mutated via append()"
    ]


NESTED_SCOPES = '''"""Class and comprehension scopes are walked through, not stopped at."""


def build(registry: dict, bucket: list, values: list) -> type:
    """Both inner mutations belong to this function, not to a nested scope."""
    [bucket.append(v) for v in values]

    class Holder:
        """Defined inline, so its body runs as part of `build`."""

        registry["built"] = 1

    return Holder


def outer(values: list) -> None:
    """A nested def owns its own mutations."""

    def inner(target: list) -> None:
        """Charged here."""
        target.append("x")

    inner(values)
'''


def test_the_walk_crosses_class_and_comprehension_scopes_but_stops_at_a_nested_def(
    corpus_of,
):
    corpus = corpus_of({"nest.py": NESTED_SCOPES})
    assert sorted(_messages(ARGUMENT, corpus)) == [
        "function 'nest:build': parameter 'bucket' mutated via append()",
        "function 'nest:build': parameter 'registry' mutated via subscript write",
        "function 'nest:outer.inner': parameter 'target' mutated via append()",
    ]


# ---------------------------------------------------------------------------
# the two leaf helpers, and the module-scope test
# ---------------------------------------------------------------------------


def test_the_two_leaf_helpers_are_not_interchangeable():
    """``_leaf_method`` is not ``leaf_name``, and one input separates them.

    On an id with neither ``"."`` nor ``":"`` — a bare module-level name —
    ``leaf_name`` returns the whole id while the frozen ``_leaf_method`` returns
    ``None``. ``no-argument-mutation`` uses the second and
    ``no-hidden-global-mutation`` the first, so collapsing them would let a bare
    id reach the argument rule's mutator table.
    """
    bare = _Row(symbol_id="items", is_attribute_access=True)
    assert _leaf_method().fn(bare) is None
    assert _leaf().fn(bare) == "items"


def test_the_leaf_method_reads_a_resolved_attribute_id_the_way_the_frozen_rule_does():
    assert _leaf_method().fn(_Row(symbol_id="m:C.append", is_attribute_access=True)) == "append"
    assert _leaf_method().fn(_Row(symbol_id="m:f:x", is_attribute_access=True)) == "x"
    assert _leaf_method().fn(_Row(symbol_id="m:C.append", is_attribute_access=False)) is None


@pytest.mark.parametrize(
    ("scope_id", "expected"),
    [
        (None, False),
        ("", True),
        ("pkg.mod", True),
        ("pkg.mod:fn", False),
    ],
)
def test_the_module_scope_test_separates_a_missing_scope_from_the_empty_one(
    scope_id, expected
):
    """``None`` is not module scope; ``""`` is.

    ``""`` is what a top-level symbol's ``parent_scope_id`` looks like in a
    source root that is itself a package (the harness's ``purity`` corpus), and
    the frozen ``_is_module_scope`` says yes to it. ``None`` is "this file
    declares no such symbol", which the frozen rules reject one line earlier as
    ``target is None: continue``. This is why the clause is an opaque and not
    ``not_(row.<field>.matches("*:*"))`` — a glob against ``None`` is ``False``,
    and the negation would admit exactly the rows the frozen rule rejects.
    """
    clause = _at_module_scope("binding_parent_scope_id")
    assert clause.fn(_Row(binding_parent_scope_id=scope_id)) is expected


# ---------------------------------------------------------------------------
# structure: where the frozen outer loop's filters sit
# ---------------------------------------------------------------------------


def _where_exprs(selection):
    return [stage.expr for stage in selection._stages if isinstance(stage, _Where)]


@pytest.mark.parametrize("build", ARGUMENT_PARTS)
def test_every_argument_part_opens_with_the_enclosing_function_and_allow_clauses(build):
    """The frozen loop filters the *function* before it looks at any reference.

    Structural equality on the expression nodes, so this asserts the real
    clauses rather than "some ``where`` exists": stage 0 is the enclosing-
    function test that stands in for ``if symbol.kind not in (FUNCTION,
    METHOD)``, and stage 1 is the negated ``allow``.
    """
    clauses = _where_exprs(build({"allow": ["pkg.mod:fn"]}))
    assert clauses[0] == row.enclosing_function_id.is_true()
    assert clauses[1] == not_(allow_by_id(("pkg.mod:fn",)))


@pytest.mark.parametrize("build", GLOBAL_REFERENCE_PARTS)
def test_every_global_reference_part_closes_its_filters_with_the_id_or_module_allow_clause(
    build,
):
    """The allow tests are the dear clauses, so they come after the cheap ones.

    A selection applies each stage to every row the stage before it kept, so
    these two clauses at the front test every reference in the project against
    every configured pattern, twice per pattern, before the kind test drops
    nearly all of them. Behind the part's own selecting clauses they see only
    rows the part could report. The clauses are the same ones, so the rows are.
    """
    clauses = _where_exprs(build({"allow": ["pkg.mod"]}))
    assert clauses[-2:] == [
        row.enclosing_function_id.is_true(),
        not_(allow_by_id_or_module(("pkg.mod",))),
    ]
    assert len(clauses) > 2, "a part with no selecting clause of its own reports every reference"


def test_the_rebind_part_carries_the_allow_clause_and_no_enclosing_function_clause():
    """A rebind row exists only where the sweep already found the function."""
    clauses = _where_exprs(global_rebind({"allow": ["pkg.mod"]}))
    assert clauses == [not_(allow_by_id_or_module(("pkg.mod",)))]


@pytest.mark.parametrize("build", (*ARGUMENT_PARTS, *GLOBAL_REFERENCE_PARTS, global_rebind))
def test_the_allow_clause_is_applied_even_with_no_patterns_configured(build):
    """An empty disjunction is ``False``, and the frozen rule still asks.

    A selection that skipped the stage when nothing is configured would claim
    the rule never evaluated its ``allow`` list, which is not what the frozen
    ``any(...)`` over an empty generator does.
    """
    clauses = _where_exprs(build({}))
    assert not_(allow_by_id(())) in clauses or not_(allow_by_id_or_module(())) in clauses


# ---------------------------------------------------------------------------
# the parts partition the rows
# ---------------------------------------------------------------------------

EVERY_SHAPE = '''"""One module carrying every shape both rules detect."""

import os

ITEMS: list = []
COUNTER = 0
CACHE: dict = {}


def touch(items: list, cfg: object, xs: list, value: object) -> None:
    """Four argument mutations and two global ones, in one body."""
    global COUNTER
    items.append(value)
    cfg.enabled = True
    xs[0] = 1
    COUNTER += 1
    CACHE["k"] = value
    ITEMS.append(value)
    os.environ["K"] = "v"


def rebind() -> None:
    """A plain rebind after `global`."""
    global COUNTER
    COUNTER = 2
'''


@pytest.mark.parametrize(
    ("rule_id", "builds"),
    [(ARGUMENT, ARGUMENT_PARTS), (GLOBAL, (*GLOBAL_REFERENCE_PARTS, global_rebind))],
)
def test_the_parts_of_a_rule_never_word_the_same_row_twice(corpus_of, rule_id, builds):
    """Disjoint by construction, asserted on rows rather than argued.

    ``MultiPartRule`` concatenates its parts, so an overlap would double-report
    silently — the partition is on ``kind`` and ``is_attribute_access`` (and, for
    the rebind part, on the row source itself), and this is the check that it
    holds on a body carrying every shape at once.
    """
    corpus = corpus_of({"every.py": EVERY_SHAPE})
    seen: set = set()
    total = 0
    for build in builds:
        anchors = {(match.anchor.kind, match.anchor.id) for match in build({}).rows(corpus)}
        assert not (anchors & seen), sorted(anchors & seen)
        seen |= anchors
        total += len(anchors)
    assert total == len(dsl_rule(rule_id).findings({}, corpus))


# ---------------------------------------------------------------------------
# the global rule's shapes, whatever the allow list, and what the allow clause costs
# ---------------------------------------------------------------------------

FOUR_SHAPES = '''"""The global rule's four shapes, beside references that are none of them."""

import os

CACHE: dict = {}
ITEMS: list = []
COUNTER = 0


def write_dict(key: str, value: object) -> None:
    """Shape 1a: a subscript write on a module-level dict."""
    CACHE[key] = value


def call_mutator(value: object) -> None:
    """Shape 2: a mutator call on a module-level list."""
    ITEMS.append(value)


def write_import(value: object) -> None:
    """Shape 3: an attribute write on an imported module."""
    os.planted_value = value


def rebind() -> None:
    """Shape 1b: a module-level variable rebound under `global`."""
    global COUNTER
    COUNTER = 1


def local_work(values: list) -> int:
    """References inside a function that no part selects."""
    seen: list = []
    total = 0
    for value in values:
        seen.append(value)
        total += len(str(value))
    return total + len(seen) + len(ITEMS)
'''

WRITE_DICT = "function 'shapes:write_dict' writes module-level variable 'shapes:CACHE'"
CALL_MUTATOR = (
    "function 'shapes:call_mutator' calls '.append()' on module-level variable 'shapes:ITEMS'"
)
WRITE_IMPORT = (
    "function 'shapes:write_import' writes attribute 'planted_value' on imported module 'os'"
)
REBIND = "function 'shapes:rebind' rebinds module-level variable 'shapes:COUNTER'"


@pytest.mark.parametrize(
    ("allow", "expected"),
    [
        ([], [WRITE_DICT, CALL_MUTATOR, WRITE_IMPORT, REBIND]),
        (["shapes:write_dict"], [CALL_MUTATOR, WRITE_IMPORT, REBIND]),
        (["shapes:call_mutator"], [WRITE_DICT, WRITE_IMPORT, REBIND]),
        (["shapes:write_import"], [WRITE_DICT, CALL_MUTATOR, REBIND]),
        (["shapes:rebind"], [WRITE_DICT, CALL_MUTATOR, WRITE_IMPORT]),
        (["shapes"], []),
        (["shapes:*"], []),
        (
            ["nope", "other:*", "shapes:local_work"],
            [WRITE_DICT, CALL_MUTATOR, WRITE_IMPORT, REBIND],
        ),
    ],
)
def test_each_shape_is_reported_once_and_silenced_only_by_its_own_allow_pattern(
    corpus_of, allow, expected
):
    """The four shapes' findings, pinned word for word (TASK-0113).

    The allow clause moved from before each part's selecting clauses to after
    them. Written first against the clauses as they stood, this is the check
    that the move changed the cost and not the rows: each shape is reported
    exactly once, a pattern for one function silences that function's shape
    and no other, and a pattern that matches nothing (here one that names a
    function none of the parts select) silences nothing.
    """
    corpus = corpus_of({"shapes.py": FOUR_SHAPES})
    assert sorted(_messages(GLOBAL, corpus, {"allow": allow})) == sorted(expected)


def _noise(modules: int) -> dict[str, str]:
    """Modules of functions whose references no global part selects."""
    body = '''"""References in function bodies that touch nothing module-level."""


def work_{i}(values: list) -> int:
    """Locals, calls and reads only."""
    seen: list = []
    total = 0
    for value in values:
        seen.append(value)
        total += len(str(value))
    return total + len(seen)
'''
    return {f"noise_{i}.py": body.format(i=i) for i in range(modules)}


@pytest.mark.parametrize("build", GLOBAL_REFERENCE_PARTS)
def test_the_allow_patterns_are_tested_only_against_rows_the_part_could_report(
    corpus_of, monkeypatch, build
):
    """Pins the cheaper evaluation, by counting fnmatch tests rather than timing.

    Every allow test is one ``fnmatchcase``; the selecting clauses make none.
    The part is run over the four shapes and then over the same four shapes
    beside forty modules of unrelated references: the count must not move.
    Opening with the allow clause (the order before TASK-0113) tests every one
    of those references, so the second count is several times the first. The
    first count must be above zero, or the pass proves nothing.
    """
    counted: list[int] = []
    real = pypeeker.dsl.expr.fnmatchcase

    def counting(name: str, pattern: str) -> bool:
        counted.append(1)
        return real(name, pattern)

    monkeypatch.setattr(pypeeker.dsl.expr, "fnmatchcase", counting)
    options = {"allow": ["nope", "other:*", "shapes:local_work"]}

    def tests_for(corpus: Corpus) -> tuple[int, int]:
        counted.clear()
        rows = build(options).rows(corpus)
        return len(counted), len(rows)

    small = tests_for(corpus_of({"shapes.py": FOUR_SHAPES}))
    large = tests_for(corpus_of({"shapes.py": FOUR_SHAPES, **_noise(40)}))
    assert small[1] == large[1] == 1, "the part reports its one shape either way"
    assert small[0] > 0
    assert large[0] == small[0]


# ---------------------------------------------------------------------------
# wording the oracle graded only on the fixture corpus
# ---------------------------------------------------------------------------

SHADOWED = '''"""Both global message shapes over one shadowed binding."""

COUNTER = 0


def rebind() -> None:
    """Shape 1b: the binder records this as a shadowed module-scope VARIABLE."""
    global COUNTER
    COUNTER = 1


def bump() -> None:
    """Shape 1a: an augmented assignment landing at module scope."""
    global COUNTER
    COUNTER += 1
'''


def test_both_global_message_shapes_strip_the_shadow_suffix(corpus_of):
    """``strip_shadow`` is graded on 1a and on 1b, which are two different fields.

    1a strips the *reference's* id, 1b the *rebound symbol's*, and only one of
    them is the shadowed binding on any given line — so a port stripping in one
    place and not the other passes half of this.
    """
    corpus = corpus_of({"sh.py": SHADOWED})
    assert sorted(_messages(GLOBAL, corpus)) == [
        "function 'sh:bump' writes module-level variable 'sh:COUNTER'",
        "function 'sh:rebind' rebinds module-level variable 'sh:COUNTER'",
    ]


METHODS = '''"""A class whose methods separate the two rules' wording."""

ITEMS: list = []


class Box:
    """Two methods, two rules, two spellings of the enclosing symbol."""

    def absorb(self, other: list) -> None:
        """The argument rule quotes the symbol's KIND: 'method'."""
        other.append(1)

    def register(self, value: object) -> None:
        """The global rule says the literal word 'function' for a method."""
        ITEMS.append(value)

    def keep(self, value: object) -> None:
        """Mutating self is not an argument mutation."""
        self.contents.append(value)
'''


def test_the_argument_rule_quotes_the_symbol_kind_and_the_global_rule_says_function(
    corpus_of,
):
    corpus = corpus_of({"meth.py": METHODS})
    assert _messages(ARGUMENT, corpus) == [
        "method 'meth:Box.absorb': parameter 'other' mutated via append()"
    ]
    assert _messages(GLOBAL, corpus) == [
        "function 'meth:Box.register' calls '.append()' on module-level variable "
        "'meth:ITEMS'"
    ]


# ---------------------------------------------------------------------------
# the options
# ---------------------------------------------------------------------------

BOTH_RULES = '''"""One function both rules would flag."""

BUFFER: list = []


def collect(items: list, value: object) -> None:
    """Mutates a parameter and a module-level container."""
    items.append(value)
    BUFFER.append(value)
'''


def test_a_module_path_pattern_silences_only_the_rule_whose_allow_reads_one(corpus_of):
    """The two ``allow`` predicates are genuinely different.

    ``no-hidden-global-mutation``'s ``_matches_any`` tests the id **and** the
    module path; ``no-argument-mutation``'s tests the id alone. A pattern that
    is a bare module path therefore silences the first and leaves the second
    firing — which is exactly what the ``mutation`` corpus's ``mut.silent``
    pattern grades, and what a port sharing one allow clause between the two
    rules would get wrong in the silent direction.
    """
    corpus = corpus_of({"both.py": BOTH_RULES})
    options = {"allow": ["both"]}
    assert _messages(GLOBAL, corpus, options) == []
    assert _messages(ARGUMENT, corpus, options) == [
        "function 'both:collect': parameter 'items' mutated via append()"
    ]


def test_an_id_glob_silences_both_rules(corpus_of):
    corpus = corpus_of({"both.py": BOTH_RULES})
    options = {"allow": ["both:*"]}
    assert _messages(ARGUMENT, corpus, options) == []
    assert _messages(GLOBAL, corpus, options) == []


EXTRA_MUTATORS = '''"""`enqueue` is not in the shared collection-mutation table."""

QUEUE: list = []


def push(sink: list, value: object) -> None:
    """One parameter mutation and one global mutation, both via `enqueue`."""
    sink.enqueue(value)
    QUEUE.enqueue(value)
'''


def test_extra_mutators_promotes_a_method_name_for_both_rules(corpus_of):
    corpus = corpus_of({"extra.py": EXTRA_MUTATORS})
    assert _messages(ARGUMENT, corpus) == []
    assert _messages(GLOBAL, corpus) == []
    options = {"extra-mutators": ["enqueue"]}
    assert _messages(ARGUMENT, corpus, options) == [
        "function 'extra:push': parameter 'sink' mutated via enqueue()"
    ]
    assert _messages(GLOBAL, corpus, options) == [
        "function 'extra:push' calls '.enqueue()' on module-level variable "
        "'extra:QUEUE'"
    ]


CHAINED = '''"""A two-segment receiver chain drops its root from the wording."""


def update(cfg: object, extra: dict) -> None:
    """The message says `options.update()`, not `cfg.options.update()`."""
    cfg.options.update(extra)
'''


def test_the_receiver_chain_wording_drops_the_root_the_message_already_names(corpus_of):
    corpus = corpus_of({"chain.py": CHAINED})
    assert _messages(ARGUMENT, corpus) == [
        "function 'chain:update': parameter 'cfg' mutated via options.update()"
    ]
