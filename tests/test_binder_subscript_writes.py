"""Tests for WRITE facts on subscript stores whose root is an attribute chain.

``x[i] = v`` on a bare name has long recorded a WRITE on the root; these
tests cover the attribute-chain extension (TASK-101): ``obj.attr[k] = v``,
``os.environ['X'] = v``, ``self.cache[k] = v`` must record a WRITE shaped
like the binder's other attribute writes (``<unresolved>.<leaf>``,
``is_attribute_access``, receiver root/chain metadata) so mutation analysis
(``analysis.writes.attribute_writes``, no-hidden-global-mutation, purity)
can see them.
"""

from __future__ import annotations

from pypeeker.analysis import ReceiverKind, attribute_writes
from pypeeker.models.references import ReferenceKind


def _refs_in(file_index, scope_id):
    return [r for r in file_index.references if r.in_scope_id == scope_id]


def _writes(refs):
    return [r for r in refs if r.kind == ReferenceKind.WRITE]


class TestAttributeChainSubscriptStore:
    def test_records_attribute_write_with_receiver_metadata(self, bind_source):
        fi = bind_source("def f(obj, k, v):\n    obj.attr[k] = v\n", "mod.py")
        writes = _writes(_refs_in(fi, "mod:f"))
        assert len(writes) == 1
        ref = writes[0]
        assert ref.symbol_id == "<unresolved>.attr"
        assert ref.kind == ReferenceKind.WRITE
        assert ref.is_attribute_access is True
        assert ref.resolved is False
        assert ref.receiver_root_symbol_id == "mod:f:obj"
        assert ref.receiver_chain == ["obj"]

    def test_receiver_root_identifier_still_emits_a_read(self, bind_source):
        # Matches visit_attribute's behavior for ``a.b = x``: the receiver
        # root is read even though the chain is written.
        fi = bind_source("def f(obj, k, v):\n    obj.attr[k] = v\n", "mod.py")
        obj_reads = [
            r
            for r in _refs_in(fi, "mod:f")
            if r.symbol_id == "mod:f:obj" and r.kind == ReferenceKind.READ
        ]
        assert len(obj_reads) == 1

    def test_deeper_attribute_chain(self, bind_source):
        fi = bind_source(
            "import a\ndef f(k, v):\n    a.b.c[k] = v\n", "mod.py"
        )
        writes = _writes(_refs_in(fi, "mod:f"))
        assert len(writes) == 1
        ref = writes[0]
        assert ref.symbol_id == "<unresolved>.c"
        assert ref.is_attribute_access is True
        assert ref.receiver_root_symbol_id == "mod:a"
        assert ref.receiver_chain == ["a", "b"]

    def test_nested_subscript_walks_to_attribute_root(self, bind_source):
        fi = bind_source(
            "def f(obj, i, j, v):\n    obj.attr[i][j] = v\n", "mod.py"
        )
        writes = _writes(_refs_in(fi, "mod:f"))
        assert len(writes) == 1
        ref = writes[0]
        assert ref.symbol_id == "<unresolved>.attr"
        assert ref.is_attribute_access is True
        assert ref.receiver_root_symbol_id == "mod:f:obj"
        assert ref.receiver_chain == ["obj"]

    def test_augmented_subscript_on_attribute_chain(self, bind_source):
        fi = bind_source(
            "def f(obj, k, v):\n    obj.attr[k] += v\n", "mod.py"
        )
        writes = _writes(_refs_in(fi, "mod:f"))
        assert len(writes) == 1
        ref = writes[0]
        assert ref.symbol_id == "<unresolved>.attr"
        assert ref.is_attribute_access is True
        assert ref.receiver_root_symbol_id == "mod:f:obj"
        assert ref.receiver_chain == ["obj"]

    def test_dynamic_root_records_no_mutation_fact(self, bind_source):
        # ``g()[k] = v`` — the chain is broken by a call; no WRITE fact,
        # same as before this feature.
        fi = bind_source("def f(k, v):\n    g()[k] = v\n", "mod.py")
        assert _writes(_refs_in(fi, "mod:f")) == []


class TestNoDuplicateRefsAtWriteSite:
    def test_each_name_at_the_write_site_is_referenced_exactly_once(
        self, bind_source
    ):
        fi = bind_source("def f(obj, k, v):\n    obj.attr[k] = v\n", "mod.py")
        # Line 1 (0-based) holds the whole statement; expect exactly:
        # READ obj, WRITE <unresolved>.attr, READ k, READ v — no extras.
        site_refs = [
            r
            for r in _refs_in(fi, "mod:f")
            if r.location.span.start.line == 1
        ]
        shapes = sorted((r.symbol_id, r.kind.value) for r in site_refs)
        assert shapes == [
            ("<unresolved>.attr", "write"),
            ("mod:f:k", "read"),
            ("mod:f:obj", "read"),
            ("mod:f:v", "read"),
        ]

    def test_attribute_leaf_has_single_reference(self, bind_source):
        fi = bind_source("def f(obj, k, v):\n    obj.attr[k] = v\n", "mod.py")
        attr_refs = [
            r for r in _refs_in(fi, "mod:f") if r.symbol_id == "<unresolved>.attr"
        ]
        assert len(attr_refs) == 1


class TestBareNameSubscriptUnchanged:
    """Regression guard: the pre-existing bare-name path is untouched."""

    def test_bare_name_subscript_store_records_root_write(self, bind_source):
        fi = bind_source("def f(x, k, v):\n    x[k] = v\n", "mod.py")
        writes = _writes(_refs_in(fi, "mod:f"))
        assert len(writes) == 1
        ref = writes[0]
        assert ref.symbol_id == "mod:f:x"
        assert ref.resolved is True
        assert ref.is_attribute_access is False
        assert ref.receiver_root_symbol_id is None
        assert ref.receiver_chain is None

    def test_bare_name_root_referenced_exactly_once(self, bind_source):
        fi = bind_source("def f(x, k, v):\n    x[k] = v\n", "mod.py")
        x_refs = [r for r in _refs_in(fi, "mod:f") if r.symbol_id == "mod:f:x"]
        assert len(x_refs) == 1
        assert x_refs[0].kind == ReferenceKind.WRITE

    def test_bare_name_augmented_subscript_store(self, bind_source):
        fi = bind_source("def f(x, k, v):\n    x[k] += v\n", "mod.py")
        writes = _writes(_refs_in(fi, "mod:f"))
        assert len(writes) == 1
        assert writes[0].symbol_id == "mod:f:x"


class TestVisibleToMutationAnalysis:
    """The new facts flow into analysis.writes.attribute_writes unchanged."""

    def test_os_environ_store_is_an_import_rooted_attribute_write(
        self, analysis_context
    ):
        ctx = analysis_context(
            "import os\ndef f(v):\n    os.environ['X'] = v\n", "mod:f"
        )
        facts = attribute_writes(ctx)
        assert len(facts) == 1
        assert facts[0].attribute == "environ"
        assert facts[0].receiver_kind == ReceiverKind.IMPORT

    def test_self_cache_store_is_a_self_rooted_attribute_write(
        self, analysis_context
    ):
        src = (
            "class C:\n"
            "    def __init__(self):\n"
            "        self.cache = {}\n"
            "    def put(self, k, v):\n"
            "        self.cache[k] = v\n"
        )
        ctx = analysis_context(src, "mod:C.put")
        facts = attribute_writes(ctx)
        assert len(facts) == 1
        assert facts[0].attribute == "cache"
        assert facts[0].receiver_kind == ReceiverKind.SELF
