"""Tests for the intent/effect/footprint protocol (TASK-87).

Covers the prefix-aware symbol-id containment rule, the footprint conflict
matrix (pure ``conflicts_with``), effect application and composition, anchor
remapping through rename/delete effects (including the epic's edge cases:
rename-of-rename, delete-vs-rename, rename-vs-delete, prefix descent),
orphan reasons, FixIntent duck-typing against a stub fix (no ``check``
import), and the frozen/hashable determinism claims.
"""

from __future__ import annotations

import dataclasses

import pytest

from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.refactor.footprint import (
    EMPTY_EFFECT,
    EMPTY_FOOTPRINT,
    ConflictKind,
    Effect,
    Footprint,
    affects,
    replace_leaf_name,
)
from pypeeker.refactor.intents import (
    DeleteSymbolIntent,
    ExtractMethodIntent,
    ExtractVariableIntent,
    FixIntent,
    InlineVariableIntent,
    OrphanedIntent,
    OrphanReason,
    PlannableFix,
    RenameIntent,
)

LIB = "def helper():\n    return 1\n"
APP = "from lib import helper\n\ndef use():\n    return helper()\n"


# ---------------------------------------------------------------------------
# Symbol-id prefix containment
# ---------------------------------------------------------------------------


class TestAffects:
    def test_exact_match(self):
        assert affects("m:Foo", "m:Foo")

    def test_class_member_descent(self):
        assert affects("m:Foo", "m:Foo.method")
        assert affects("m:Foo", "m:Foo.Inner.method")

    def test_local_descent_via_colon(self):
        assert affects("m:f", "m:f:x")

    def test_module_prefix_affects_its_symbols(self):
        assert affects("pkg.mod", "pkg.mod:X")

    def test_string_prefix_without_separator_is_not_containment(self):
        assert not affects("m:Foo", "m:Foobar")
        assert not affects("pkg.mod", "pkg.module:X")

    def test_shadow_suffix_is_a_distinct_binding(self):
        assert not affects("m:f:x", "m:f:x$2")

    def test_descendant_does_not_affect_ancestor(self):
        assert not affects("m:Foo.method", "m:Foo")


class TestReplaceLeafName:
    def test_method_leaf(self):
        assert replace_leaf_name("m:Foo.method", "run") == "m:Foo.run"

    def test_local_leaf_preserves_shadow_suffix(self):
        assert replace_leaf_name("m:f:x$2", "y") == "m:f:y$2"

    def test_module_level_symbol(self):
        assert replace_leaf_name("lib:helper", "do_help") == "lib:do_help"

    def test_bare_module_path(self):
        assert replace_leaf_name("pkg.mod", "new") == "pkg.new"


# ---------------------------------------------------------------------------
# Footprint conflict matrix
# ---------------------------------------------------------------------------


class TestFootprintConflicts:
    def test_disjoint_footprints_do_not_conflict(self):
        a = Footprint(writes_symbols={"m:a"}, writes_files={"a.py"})
        b = Footprint(writes_symbols={"n:b"}, writes_files={"b.py"})
        assert a.conflicts_with(b) is None

    def test_write_write_file_overlap(self):
        a = Footprint(writes_files={"m.py"})
        b = Footprint(writes_files={"m.py"})
        report = a.conflicts_with(b)
        assert report is not None
        assert report.kind is ConflictKind.WRITE_WRITE
        assert (report.dimension, report.items) == ("files", ("m.py",))

    def test_write_read_file_overlap_either_direction(self):
        writer = Footprint(writes_files={"m.py"})
        reader = Footprint(reads_files={"m.py"})
        for a, b in ((writer, reader), (reader, writer)):
            report = a.conflicts_with(b)
            assert report is not None
            assert report.kind is ConflictKind.WRITE_READ
            assert report.dimension == "files"

    def test_write_write_symbol_overlap_exact(self):
        a = Footprint(writes_symbols={"m:Foo"})
        b = Footprint(writes_symbols={"m:Foo"})
        report = a.conflicts_with(b)
        assert report is not None
        assert report.kind is ConflictKind.WRITE_WRITE
        assert report.dimension == "symbols"

    def test_symbol_overlap_is_prefix_aware_both_directions(self):
        parent = Footprint(writes_symbols={"m:Foo"})
        child = Footprint(writes_symbols={"m:Foo.method"})
        for a, b in ((parent, child), (child, parent)):
            report = a.conflicts_with(b)
            assert report is not None
            assert set(report.items) == {"m:Foo", "m:Foo.method"}

    def test_foo_vs_foobar_do_not_overlap(self):
        a = Footprint(writes_symbols={"m:Foo"})
        b = Footprint(writes_symbols={"m:Foobar"})
        assert a.conflicts_with(b) is None

    def test_write_vs_read_symbol_descendant(self):
        writer = Footprint(writes_symbols={"m:Foo"})
        reader = Footprint(reads_symbols={"m:Foo.method"})
        report = writer.conflicts_with(reader)
        assert report is not None
        assert report.kind is ConflictKind.WRITE_READ
        assert report.dimension == "symbols"

    def test_scoped_fact_invalidated_by_symbol_write(self):
        writer = Footprint(writes_symbols={"m:f"})
        reader = Footprint(reads_facts={"purity:m:f"})
        report = writer.conflicts_with(reader)
        assert report is not None
        assert report.kind is ConflictKind.WRITE_READ
        assert (report.dimension, report.items) == ("facts", ("purity:m:f",))

    def test_scoped_fact_with_unrelated_scope_does_not_conflict(self):
        writer = Footprint(writes_symbols={"m:f"})
        reader = Footprint(reads_facts={"purity:m:g"})
        assert writer.conflicts_with(reader) is None

    def test_scoped_fact_not_invalidated_by_file_write(self):
        # Documented granularity: file writes do not map to symbol scopes.
        writer = Footprint(writes_files={"m.py"})
        reader = Footprint(reads_facts={"purity:m:f"})
        assert writer.conflicts_with(reader) is None

    def test_global_fact_invalidated_by_any_write(self):
        reader = Footprint(reads_facts={"callgraph"})
        assert reader.conflicts_with(Footprint(writes_files={"x.py"})) is not None
        assert reader.conflicts_with(Footprint(writes_symbols={"m:x"})) is not None
        assert reader.conflicts_with(EMPTY_FOOTPRINT) is None

    def test_conflicts_with_is_symmetric(self):
        a = Footprint(writes_symbols={"m:Foo"}, writes_files={"m.py"})
        b = Footprint(reads_symbols={"m:Foo.method"})
        ab, ba = a.conflicts_with(b), b.conflicts_with(a)
        assert ab == ba

    def test_pure_no_inputs_mutated(self):
        a = Footprint(writes_symbols={"m:Foo"})
        b = Footprint(writes_symbols={"m:Foo"})
        a.conflicts_with(b)
        assert a == Footprint(writes_symbols={"m:Foo"})
        assert b == Footprint(writes_symbols={"m:Foo"})


# ---------------------------------------------------------------------------
# Effects: application and composition
# ---------------------------------------------------------------------------


class TestEffectRemap:
    def test_exact_rename(self):
        effect = Effect(renamed={"m:A": "m:B"})
        assert effect.remap_id("m:A") == "m:B"

    def test_prefix_descent(self):
        effect = Effect(renamed={"m:Foo": "m:Bar"})
        assert effect.remap_id("m:Foo.method") == "m:Bar.method"

    def test_unmentioned_id_unchanged(self):
        effect = Effect(renamed={"m:Foo": "m:Bar"})
        assert effect.remap_id("m:Other") == "m:Other"
        assert effect.remap_id("m:Foobar") == "m:Foobar"

    def test_deleted_id_maps_to_none(self):
        effect = Effect(deleted={"m:Foo"})
        assert effect.remap_id("m:Foo") is None

    def test_deletion_is_prefix_aware(self):
        effect = Effect(deleted={"m:Foo"})
        assert effect.remap_id("m:Foo.method") is None
        assert effect.remap_id("m:Foobar") == "m:Foobar"

    def test_exact_rename_beats_prefix_descent(self):
        effect = Effect(renamed={"m:Foo": "m:Bar", "m:Foo.method": "m:Bar.run"})
        assert effect.remap_id("m:Foo.method") == "m:Bar.run"

    def test_file_rename(self):
        effect = Effect(files_renamed={"old.py": "new.py"})
        assert effect.remap_file("old.py") == "new.py"
        assert effect.remap_file("other.py") == "other.py"


class TestEffectComposition:
    def test_rename_of_rename_composes(self):
        e1 = Effect(renamed={"m:A": "m:B"})
        e2 = Effect(renamed={"m:B": "m:C"})
        composed = e1.then(e2)
        assert composed.remap_id("m:A") == "m:C"
        assert ("m:A", "m:C") in composed.renamed

    def test_composition_matches_sequential_application(self):
        e1 = Effect(renamed={"m:Foo": "m:Bar"}, deleted={"m:gone"})
        e2 = Effect(renamed={"m:Bar.method": "m:Bar.run"}, deleted={"m:Bar.dead"})
        composed = e1.then(e2)
        for symbol_id in (
            "m:Foo",
            "m:Foo.method",
            "m:Foo.dead",
            "m:Foo.other",
            "m:gone",
            "m:unrelated",
        ):
            step1 = e1.remap_id(symbol_id)
            sequential = e2.remap_id(step1) if step1 is not None else None
            assert composed.remap_id(symbol_id) == sequential, symbol_id

    def test_rename_then_delete_becomes_delete_of_original(self):
        e1 = Effect(renamed={"m:A": "m:B"})
        e2 = Effect(deleted={"m:B"})
        composed = e1.then(e2)
        assert composed.remap_id("m:A") is None
        assert "m:A" in composed.deleted

    def test_created_then_deleted_vanishes(self):
        e1 = Effect(created={"m:new"})
        e2 = Effect(deleted={"m:new"})
        composed = e1.then(e2)
        assert "m:new" not in composed.created
        assert "m:new" not in composed.deleted

    def test_created_id_follows_later_rename(self):
        e1 = Effect(created={"m:new"})
        e2 = Effect(renamed={"m:new": "m:newer"})
        assert e1.then(e2).created == frozenset({"m:newer"})

    def test_files_written_follow_file_renames(self):
        e1 = Effect(files_written={"old.py"})
        e2 = Effect(files_renamed={"old.py": "new.py"}, files_written={"new.py"})
        composed = e1.then(e2)
        assert composed.files_written == frozenset({"new.py"})
        assert ("old.py", "new.py") in composed.files_renamed

    def test_empty_effect_is_identity(self):
        effect = Effect(
            renamed={"m:A": "m:B"},
            deleted={"m:dead"},
            created={"m:new"},
            files_written={"m.py"},
            files_renamed={"a.py": "b.py"},
        )
        assert EMPTY_EFFECT.then(effect) == effect
        assert effect.then(EMPTY_EFFECT) == effect


# ---------------------------------------------------------------------------
# Anchor remapping on intents
# ---------------------------------------------------------------------------


class TestIntentRemap:
    def test_rename_intent_follows_exact_rename(self):
        intent = RenameIntent("i1", "m:A", "C")
        remapped = intent.remap(Effect(renamed={"m:A": "m:B"}))
        assert isinstance(remapped, RenameIntent)
        assert remapped.symbol_id == "m:B"
        assert (remapped.intent_id, remapped.new_name) == ("i1", "C")

    def test_prefix_descent_remaps_member_anchor(self):
        # rename m:Foo -> m:Bar remaps an intent anchored at m:Foo.method
        intent = RenameIntent("i1", "m:Foo.method", "run")
        remapped = intent.remap(Effect(renamed={"m:Foo": "m:Bar"}))
        assert isinstance(remapped, RenameIntent)
        assert remapped.symbol_id == "m:Bar.method"

    def test_delete_vs_rename_orphans_the_rename(self):
        intent = RenameIntent("i1", "m:Foo", "Bar")
        orphan = intent.remap(Effect(deleted={"m:Foo"}))
        assert isinstance(orphan, OrphanedIntent)
        assert orphan.reason is OrphanReason.ANCHOR_DELETED
        assert orphan.intent is intent
        assert "m:Foo" in orphan.detail

    def test_prefix_delete_orphans_member_anchor(self):
        intent = InlineVariableIntent("i1", "m:f:x")
        orphan = intent.remap(Effect(deleted={"m:f"}))
        assert isinstance(orphan, OrphanedIntent)
        assert orphan.reason is OrphanReason.ANCHOR_DELETED

    def test_rename_vs_delete_remaps_the_delete(self):
        intent = DeleteSymbolIntent("i1", "m:Foo")
        remapped = intent.remap(Effect(renamed={"m:Foo": "m:Bar"}))
        assert isinstance(remapped, DeleteSymbolIntent)
        assert remapped.symbol_id == "m:Bar"

    def test_untouched_anchor_returns_same_intent(self):
        intent = RenameIntent("i1", "m:Other", "x")
        assert intent.remap(Effect(renamed={"m:Foo": "m:Bar"})) is intent

    def test_options_and_deps_survive_remap(self):
        intent = RenameIntent(
            "i1", "m:A", "B", include_exports=True, deps={"i0"}
        )
        remapped = intent.remap(Effect(renamed={"m:A": "m:A2"}))
        assert isinstance(remapped, RenameIntent)
        assert remapped.include_exports is True
        assert remapped.deps == frozenset({"i0"})

    def test_extract_intents_follow_file_renames(self):
        rename = Effect(files_renamed={"old.py": "new.py"})
        var = ExtractVariableIntent("i1", "old.py", (1, 0), (1, 5), "x")
        method = ExtractMethodIntent("i2", "old.py", 1, 3, "f")
        var2, method2 = var.remap(rename), method.remap(rename)
        assert isinstance(var2, ExtractVariableIntent)
        assert isinstance(method2, ExtractMethodIntent)
        assert (var2.file_path, method2.file_path) == ("new.py", "new.py")
        # positions/lines are not remapped (guarded revalidation's job)
        assert (var2.start, var2.end) == ((1, 0), (1, 5))
        assert (method2.start_line, method2.end_line) == (1, 3)

    def test_rename_of_rename_remaps_cleanly_end_to_end(self, indexed_project):
        # Effect A->B applied to a pending intent anchored at A renaming to C:
        # the substitutions compose to A->C overall.
        _, store = indexed_project({"lib.py": LIB, "app.py": APP})
        first = RenameIntent("i1", "lib:helper", "assist")
        e1 = first.predicted_effect(store)
        pending = RenameIntent("i2", "lib:helper", "do_help")
        remapped = pending.remap(e1)
        assert isinstance(remapped, RenameIntent)
        assert remapped.symbol_id == "lib:assist"
        e2 = Effect(renamed={"lib:assist": "lib:do_help"})
        assert e1.then(e2).remap_id("lib:helper") == "lib:do_help"


# ---------------------------------------------------------------------------
# Intent footprints and predicted effects against a real store
# ---------------------------------------------------------------------------


class TestIntentFootprints:
    def test_rename_footprint_spans_definition_importers_and_references(
        self, indexed_project
    ):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP})
        footprint = RenameIntent("i1", "lib:helper", "do_help").footprint(store)
        assert footprint.writes_symbols == frozenset({"lib:helper"})
        assert {"lib.py", "app.py"} <= set(footprint.writes_files)
        assert footprint.writes_files == footprint.reads_files

    def test_rename_footprint_degrades_when_anchor_unresolvable(
        self, indexed_project
    ):
        _, store = indexed_project({"lib.py": LIB})
        footprint = RenameIntent("i1", "lib:nonexistent", "x").footprint(store)
        assert footprint.writes_symbols == frozenset({"lib:nonexistent"})
        assert footprint.writes_files == frozenset()

    def test_rename_predicted_effect_substitutes_leaf(self, indexed_project):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP})
        effect = RenameIntent("i1", "lib:helper", "do_help").predicted_effect(store)
        assert ("lib:helper", "lib:do_help") in effect.renamed
        assert {"lib.py", "app.py"} <= set(effect.files_written)
        assert effect.deleted == frozenset()

    def test_rename_vs_edit_same_file_conflicts(self, indexed_project):
        _, store = indexed_project({"lib.py": LIB})
        rename = RenameIntent("i1", "lib:helper", "do_help")
        edit = FixIntent("i2", _StubFix(_StubPlan([_edit("lib.py")])))
        report = rename.footprint(store).conflicts_with(edit.footprint(store))
        assert report is not None
        assert report.kind is ConflictKind.WRITE_WRITE
        assert (report.dimension, report.items) == ("files", ("lib.py",))

    def test_delete_vs_rename_same_symbol_conflicts(self, indexed_project):
        _, store = indexed_project({"lib.py": LIB})
        delete = DeleteSymbolIntent("i1", "lib:helper")
        rename = RenameIntent("i2", "lib:helper", "do_help")
        report = delete.footprint(store).conflicts_with(rename.footprint(store))
        assert report is not None
        assert report.kind is ConflictKind.WRITE_WRITE
        assert report.dimension == "symbols"

    def test_disjoint_intents_do_not_conflict(self, indexed_project):
        _, store = indexed_project(
            {"lib.py": LIB, "other.py": "def standalone():\n    return 2\n"}
        )
        a = RenameIntent("i1", "lib:helper", "do_help")
        b = RenameIntent("i2", "other:standalone", "alone")
        assert a.footprint(store).conflicts_with(b.footprint(store)) is None

    def test_inline_variable_footprint_and_effect(self, indexed_project):
        src = "def f():\n    x = 1\n    return x\n"
        _, store = indexed_project({"mod.py": src})
        intent = InlineVariableIntent("i1", "mod:f:x")
        footprint = intent.footprint(store)
        assert footprint.writes_symbols == frozenset({"mod:f:x"})
        assert footprint.writes_files == frozenset({"mod.py"})
        assert "purity:mod:f:x" in footprint.reads_facts
        effect = intent.predicted_effect(store)
        assert effect.deleted == frozenset({"mod:f:x"})
        assert effect.files_written == frozenset({"mod.py"})

    def test_extract_intents_are_file_level(self, indexed_project):
        _, store = indexed_project({"mod.py": "def f():\n    y = 1 + 2\n    return y\n"})
        var = ExtractVariableIntent("i1", "mod.py", (1, 8), (1, 13), "total")
        assert var.footprint(store).writes_files == frozenset({"mod.py"})
        assert var.predicted_effect(store) == Effect(files_written={"mod.py"})
        method = ExtractMethodIntent("i2", "mod.py", 1, 1, "compute")
        effect = method.predicted_effect(store)
        assert effect.files_written == frozenset({"mod.py"})
        assert effect.created == frozenset({"mod:compute"})

    def test_extract_method_created_id_degrades_without_index(self, indexed_project):
        _, store = indexed_project({"mod.py": "def f():\n    pass\n"})
        method = ExtractMethodIntent("i1", "unknown.py", 0, 0, "g")
        assert method.predicted_effect(store).created == frozenset()


# ---------------------------------------------------------------------------
# FixIntent duck-typing (no check import)
# ---------------------------------------------------------------------------


def _edit(file: str) -> EditEntry:
    """A minimal EditEntry touching ``file``."""
    return EditEntry(
        op=EditOp.REPLACE, file=file, start=0, end=3,
        old="def", new="DEF", file_hash="0" * 64,
    )


@dataclasses.dataclass(frozen=True)
class _StubPlan:
    """Plan-shaped stub: anything with an ``edits`` list counts as planned."""

    edits: tuple[EditEntry, ...]


@dataclasses.dataclass(frozen=True)
class _StubDecline:
    """Decline-shaped stub: no ``edits`` attribute, mirroring FixDeclined."""

    reason: str


@dataclasses.dataclass(frozen=True)
class _StubFix:
    """Duck-typed fix: satisfies PlannableFix without importing check."""

    result: object
    fix_id: str = "stub:fix"
    description: str = "a stub fix"

    def plan(self, store) -> object:
        """Return the canned result regardless of state."""
        return self.result


class TestFixIntent:
    def test_stub_fix_satisfies_the_structural_protocol(self):
        assert isinstance(_StubFix(_StubDecline("x")), PlannableFix)

    def test_footprint_covers_planned_edit_files(self, indexed_project):
        _, store = indexed_project({"lib.py": LIB})
        fix = _StubFix(_StubPlan((_edit("lib.py"), _edit("app.py"))))
        intent = FixIntent("i1", fix)
        footprint = intent.footprint(store)
        assert footprint.writes_files == frozenset({"lib.py", "app.py"})
        assert footprint.reads_files == footprint.writes_files
        assert footprint.writes_symbols == frozenset()
        assert intent.predicted_effect(store).files_written == footprint.writes_files

    def test_declined_fix_yields_empty_footprint(self, indexed_project):
        _, store = indexed_project({"lib.py": LIB})
        intent = FixIntent("i1", _StubFix(_StubDecline("text-mismatch")))
        assert intent.footprint(store) == EMPTY_FOOTPRINT
        assert intent.predicted_effect(store) == EMPTY_EFFECT

    def test_remap_is_identity(self):
        intent = FixIntent("i1", _StubFix(_StubDecline("x")))
        effect = Effect(renamed={"m:A": "m:B"}, deleted={"m:C"})
        assert intent.remap(effect) is intent


# ---------------------------------------------------------------------------
# Determinism: frozen / hashable / normalised construction
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_footprint_is_hashable_and_order_insensitive(self):
        a = Footprint(writes_symbols={"m:a", "m:b"}, reads_files=["x.py", "y.py"])
        b = Footprint(writes_symbols=["m:b", "m:a"], reads_files={"y.py", "x.py"})
        assert a == b
        assert hash(a) == hash(b)

    def test_effect_is_hashable_and_order_insensitive(self):
        a = Effect(renamed={"m:a": "m:b", "m:c": "m:d"}, deleted={"m:x", "m:y"})
        b = Effect(renamed=[("m:c", "m:d"), ("m:a", "m:b")], deleted=["m:y", "m:x"])
        assert a == b
        assert hash(a) == hash(b)

    def test_footprint_and_effect_are_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            Footprint().reads_files = frozenset()  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            Effect().deleted = frozenset()  # type: ignore[misc]

    def test_intents_are_frozen_and_hashable(self):
        intent = RenameIntent("i1", "m:A", "B", deps=["i0"])
        assert intent.deps == frozenset({"i0"})
        assert intent == RenameIntent("i1", "m:A", "B", deps={"i0"})
        assert hash(intent) == hash(RenameIntent("i1", "m:A", "B", deps={"i0"}))
        with pytest.raises(dataclasses.FrozenInstanceError):
            intent.new_name = "C"  # type: ignore[misc]

    def test_intent_kinds_are_stable(self):
        assert RenameIntent.kind == "rename"
        assert InlineVariableIntent.kind == "inline-variable"
        assert ExtractVariableIntent.kind == "extract-variable"
        assert ExtractMethodIntent.kind == "extract-method"
        assert DeleteSymbolIntent.kind == "delete-symbol"
        assert FixIntent.kind == "edit"

    def test_conflict_report_items_are_sorted(self):
        a = Footprint(writes_files={"b.py", "a.py"})
        b = Footprint(writes_files={"a.py", "b.py"})
        report = a.conflicts_with(b)
        assert report is not None
        assert report.items == ("a.py", "b.py")
