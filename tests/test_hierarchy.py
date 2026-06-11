"""Tests for class hierarchy facts (pypeeker.analysis.hierarchy, TASK-94)."""

from pypeeker.analysis import BaseRef, Hierarchy


def build_hierarchy(indexed_project, files):
    _, store = indexed_project(files)
    return Hierarchy.from_store(store)


class TestSingleInheritance:
    FILES = {
        "mod.py": (
            "class Base:\n"
            "    def area(self):\n"
            "        return 0\n"
            "\n"
            "    def name(self):\n"
            "        return ''\n"
            "\n"
            "class Child(Base):\n"
            "    def area(self):\n"
            "        return 3\n"
        )
    }

    def test_bases_resolved(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.bases("mod:Child") == [BaseRef(text="Base", class_id="mod:Base")]
        assert h.bases("mod:Child")[0].known

    def test_root_class_has_no_bases(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.bases("mod:Base") == []

    def test_overrides(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.overrides("mod:Child.area") == ["mod:Base.area"]

    def test_overridden_by(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.overridden_by("mod:Base.area") == ["mod:Child.area"]

    def test_non_overridden_method_has_no_edges(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.overrides("mod:Base.name") == []
        assert h.overridden_by("mod:Base.name") == []

    def test_mro_known(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.mro_unknown("mod:Base") is False
        assert h.mro_unknown("mod:Child") is False

    def test_unknown_ids_are_conservative(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.mro_unknown("mod:NoSuchClass") is True
        assert h.overrides("mod:NoSuchClass.m") == []
        assert h.overridden_by("mod:NoSuchClass.m") == []


class TestCrossModuleBases:
    def test_base_imported_directly(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "base.py": "class Base:\n    def run(self):\n        return 0\n",
                "child.py": (
                    "from base import Base\n"
                    "\n"
                    "class Child(Base):\n"
                    "    def run(self):\n"
                    "        return 1\n"
                ),
            },
        )
        assert h.bases("child:Child") == [
            BaseRef(text="Base", class_id="base:Base")
        ]
        assert h.overrides("child:Child.run") == ["base:Base.run"]
        assert h.overridden_by("base:Base.run") == ["child:Child.run"]
        assert h.mro_unknown("child:Child") is False

    def test_base_imported_through_barrel(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "pkg/__init__.py": "from pkg.impl import Base\n",
                "pkg/impl.py": (
                    "class Base:\n    def run(self):\n        return 0\n"
                ),
                "consumer.py": (
                    "from pkg import Base\n"
                    "\n"
                    "class Child(Base):\n"
                    "    def run(self):\n"
                    "        return 1\n"
                ),
            },
        )
        assert h.bases("consumer:Child") == [
            BaseRef(text="Base", class_id="pkg.impl:Base")
        ]
        assert h.overrides("consumer:Child.run") == ["pkg.impl:Base.run"]
        assert h.overridden_by("pkg.impl:Base.run") == ["consumer:Child.run"]


class TestProtocolImplementation:
    """Implementing a *project* Protocol class is an ordinary override edge."""

    FILES = {
        "proto.py": (
            "from typing import Protocol\n"
            "\n"
            "class Reader(Protocol):\n"
            "    def read(self):\n"
            "        ...\n"
        ),
        "impl.py": (
            "from proto import Reader\n"
            "\n"
            "class FileReader(Reader):\n"
            "    def read(self):\n"
            "        return 1\n"
        ),
    }

    def test_implementation_edge_detected(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.overrides("impl:FileReader.read") == ["proto:Reader.read"]
        assert h.overridden_by("proto:Reader.read") == ["impl:FileReader.read"]

    def test_protocol_base_itself_is_unknown_external(self, indexed_project):
        h = build_hierarchy(indexed_project, self.FILES)
        assert h.bases("proto:Reader") == [BaseRef(text="Protocol", class_id=None)]
        # Incompleteness propagates down the chain — consumers stay conservative.
        assert h.mro_unknown("proto:Reader") is True
        assert h.mro_unknown("impl:FileReader") is True


class TestUnknownBases:
    def test_stdlib_dotted_base_is_unknown(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {"mod.py": "import abc\n\nclass Svc(abc.ABC):\n    pass\n"},
        )
        assert h.bases("mod:Svc") == [BaseRef(text="abc.ABC", class_id=None)]
        assert h.mro_unknown("mod:Svc") is True

    def test_builtin_base_is_unknown(self, indexed_project):
        h = build_hierarchy(
            indexed_project, {"mod.py": "class Meta(type):\n    pass\n"}
        )
        assert h.bases("mod:Meta") == [BaseRef(text="type", class_id=None)]
        assert h.mro_unknown("mod:Meta") is True

    def test_dynamic_bases_are_unknown(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "def make_base():\n"
                    "    return object\n"
                    "\n"
                    "class Dyn(make_base()):\n"
                    "    pass\n"
                )
            },
        )
        assert h.bases("mod:Dyn") == [BaseRef(text="make_base()", class_id=None)]
        assert h.mro_unknown("mod:Dyn") is True


class TestHeaderDiscrimination:
    def test_metaclass_keyword_is_not_a_base(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "class Meta(type):\n"
                    "    pass\n"
                    "\n"
                    "class Base:\n"
                    "    pass\n"
                    "\n"
                    "class C(Base, metaclass=Meta):\n"
                    "    pass\n"
                )
            },
        )
        assert h.bases("mod:C") == [BaseRef(text="Base", class_id="mod:Base")]

    def test_subscripted_generic_strips_to_base_name(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "class Base:\n"
                    "    def get(self):\n"
                    "        return None\n"
                    "\n"
                    "class C(Base[int]):\n"
                    "    def get(self):\n"
                    "        return 1\n"
                )
            },
        )
        assert h.bases("mod:C") == [BaseRef(text="Base[int]", class_id="mod:Base")]
        assert h.overrides("mod:C.get") == ["mod:Base.get"]
        assert h.mro_unknown("mod:C") is False

    def test_multiline_header_with_comment(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "class A:\n"
                    "    pass\n"
                    "\n"
                    "class B:\n"
                    "    pass\n"
                    "\n"
                    "class C(\n"
                    "    A,  # first base\n"
                    "    B,\n"
                    "):\n"
                    "    pass\n"
                )
            },
        )
        assert h.bases("mod:C") == [
            BaseRef(text="A", class_id="mod:A"),
            BaseRef(text="B", class_id="mod:B"),
        ]

    def test_class_body_references_are_not_bases(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "class A:\n"
                    "    pass\n"
                    "\n"
                    "class B:\n"
                    "    x = A\n"
                )
            },
        )
        assert h.bases("mod:B") == []


class TestCycleAndDepthSafety:
    def test_cross_module_cycle_terminates_and_marks_unknown(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "a.py": "from b import B\n\nclass A(B):\n    pass\n",
                "b.py": "from a import A\n\nclass B(A):\n    pass\n",
            },
        )
        assert h.mro_unknown("a:A") is True
        assert h.mro_unknown("b:B") is True

    def test_overrides_found_across_a_deep_but_capped_chain(self, indexed_project):
        lines = ["class C0:\n    def m(self):\n        return 0\n"]
        for i in range(1, 5):
            lines.append(f"\nclass C{i}(C{i - 1}):\n    pass\n")
        lines.append("\nclass Leaf(C4):\n    def m(self):\n        return 1\n")
        h = build_hierarchy(indexed_project, {"mod.py": "".join(lines)})
        assert h.overrides("mod:Leaf.m") == ["mod:C0.m"]
        assert h.overridden_by("mod:C0.m") == ["mod:Leaf.m"]
        assert h.mro_unknown("mod:Leaf") is False

    def test_chain_deeper_than_cap_marks_unknown(self, indexed_project):
        depth = Hierarchy._MAX_DEPTH + 8
        lines = ["class C0:\n    pass\n"]
        for i in range(1, depth):
            lines.append(f"\nclass C{i}(C{i - 1}):\n    pass\n")
        h = build_hierarchy(indexed_project, {"mod.py": "".join(lines)})
        assert h.mro_unknown(f"mod:C{depth - 1}") is True
        assert h.mro_unknown("mod:C5") is False


class TestNameMangledPrivates:
    def test_mangled_private_methods_never_pair(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "class A:\n"
                    "    def __secret(self):\n"
                    "        return 0\n"
                    "\n"
                    "class B(A):\n"
                    "    def __secret(self):\n"
                    "        return 1\n"
                )
            },
        )
        # Name mangling makes these distinct attributes — no override contract.
        assert h.overrides("mod:B.__secret") == []
        assert h.overridden_by("mod:A.__secret") == []

    def test_dunder_methods_do_pair(self, indexed_project):
        h = build_hierarchy(
            indexed_project,
            {
                "mod.py": (
                    "class A:\n"
                    "    def __len__(self):\n"
                    "        return 0\n"
                    "\n"
                    "class B(A):\n"
                    "    def __len__(self):\n"
                    "        return 1\n"
                )
            },
        )
        assert h.overrides("mod:B.__len__") == ["mod:A.__len__"]


class TestSourceUnavailable:
    def test_unreadable_header_degrades_to_unknown(self, indexed_project):
        from pypeeker.resolve import CrossModuleResolver

        _, store = indexed_project(
            {"mod.py": "class A:\n    pass\n\nclass B(A):\n    pass\n"}
        )
        indexes = [store.load(fp) for fp in store.list_indexed_files()]
        resolver = CrossModuleResolver(indexes)
        h = Hierarchy.build(indexes, resolver, read_source=None)
        assert h.bases("mod:B") == [BaseRef(text="<unreadable header>")]
        assert h.mro_unknown("mod:B") is True
        # A class with no header references needs no source at all.
        assert h.bases("mod:A") == []
        assert h.mro_unknown("mod:A") is False
