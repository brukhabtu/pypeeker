"""Tests for receiver-chain metadata on attribute-access references.

Verifies the binder correctly populates ``receiver_root_symbol_id`` and
``receiver_chain`` for attribute-access calls and reads.
"""

from __future__ import annotations

import pytest


def _attr_refs_in(file_index, scope_id):
    return [
        r for r in file_index.references
        if r.in_scope_id == scope_id and r.is_attribute_access
    ]


def _by_leaf(refs, leaf_name):
    target = f"<unresolved>.{leaf_name}"
    matching = [r for r in refs if r.symbol_id == target]
    assert len(matching) == 1, (
        f"expected exactly one reference for {target!r}; got {len(matching)}"
    )
    return matching[0]


class TestImportRootedReceivers:
    def test_module_attribute_call(self, bind_source):
        fi = bind_source(
            "import os\ndef f():\n    os.system('ls')\n", "mod.py"
        )
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "system")
        assert ref.receiver_root_symbol_id == "mod.py:os"
        assert ref.receiver_chain == ["os"]

    def test_submodule_attribute_call(self, bind_source):
        fi = bind_source(
            "import os\ndef f():\n    os.path.join('a', 'b')\n", "mod.py"
        )
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "join")
        assert ref.receiver_root_symbol_id == "mod.py:os"
        assert ref.receiver_chain == ["os", "path"]


class TestLocalRootedReceivers:
    def test_parameter_attribute_call(self, bind_source):
        fi = bind_source(
            "def f(path):\n    path.write_text('x')\n", "mod.py"
        )
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "write_text")
        assert ref.receiver_root_symbol_id == "mod.py:f:path"
        assert ref.receiver_chain == ["path"]

    def test_local_variable_attribute_call(self, bind_source):
        fi = bind_source(
            "def f():\n    p = make()\n    p.write_text('x')\n", "mod.py"
        )
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "write_text")
        assert ref.receiver_root_symbol_id == "mod.py:f:p"
        assert ref.receiver_chain == ["p"]


class TestDynamicReceivers:
    def test_call_result_breaks_chain(self, bind_source):
        fi = bind_source("def f():\n    g().bar()\n", "mod.py")
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "bar")
        assert ref.receiver_root_symbol_id is None
        assert ref.receiver_chain is None

    def test_subscript_breaks_chain(self, bind_source):
        fi = bind_source("def f(lst):\n    lst[0].method()\n", "mod.py")
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "method")
        assert ref.receiver_root_symbol_id is None
        assert ref.receiver_chain is None


class TestUnresolvedRoot:
    def test_chain_kept_even_when_root_does_not_resolve(self, bind_source):
        fi = bind_source(
            "def f():\n    unknown.bar()\n", "mod.py"
        )
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), "bar")
        assert ref.receiver_root_symbol_id is None
        assert ref.receiver_chain == ["unknown"]


class TestSelfAttribute:
    def test_self_method_call(self, bind_source):
        fi = bind_source(
            "class C:\n    def m(self):\n        self.helper()\n"
            "    def helper(self):\n        pass\n",
            "mod.py",
        )
        ref = _by_leaf(_attr_refs_in(fi, "mod.py:C.m"), "helper")
        # self resolves to the parameter; chain captures it.
        assert ref.receiver_chain == ["self"]
        assert ref.receiver_root_symbol_id is not None
        assert "self" in ref.receiver_root_symbol_id


class TestAttributeWriteCarriesReceiverInfo:
    def test_attribute_write(self, bind_source):
        fi = bind_source(
            "class Box:\n    def set(self, v):\n        self.value = v\n",
            "mod.py",
        )
        # The WRITE is on `self.value` (attribute leaf 'value').
        refs = [
            r for r in fi.references
            if r.in_scope_id == "mod.py:Box.set"
            and r.is_attribute_access
            and r.kind.value == "write"
        ]
        assert len(refs) == 1
        assert refs[0].receiver_chain == ["self"]


@pytest.mark.parametrize(
    "src,leaf,expected_chain",
    [
        (
            "import time\ndef f():\n    time.time()\n",
            "time",
            ["time"],
        ),
        (
            "import random\ndef f():\n    random.random()\n",
            "random",
            ["random"],
        ),
        (
            "import os\ndef f():\n    os.path.exists('x')\n",
            "exists",
            ["os", "path"],
        ),
    ],
)
def test_module_calls_have_receiver_chain(bind_source, src, leaf, expected_chain):
    fi = bind_source(src, "mod.py")
    ref = _by_leaf(_attr_refs_in(fi, "mod.py:f"), leaf)
    assert ref.receiver_chain == expected_chain
    # Root resolves to the import (its symbol_id ends with the imported name).
    assert ref.receiver_root_symbol_id is not None
    assert ref.receiver_root_symbol_id.endswith(f":{expected_chain[0]}")
