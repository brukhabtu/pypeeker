"""Unit tests for the symbol-id grammar module (pypeeker.models.symbol_id)."""

from pypeeker.models import (
    BUILTINS_PREFIX,
    UNRESOLVED_PREFIX,
    FileIndex,
    builtin_id,
    builtin_name,
    is_builtin,
    is_unresolved_attr,
    leaf_name,
    module_of,
    module_symbol_id,
    shadow_id,
    shadow_suffix,
    strip_shadow,
    unresolved_attr_id,
    unresolved_attr_name,
)


class TestPrefixConstants:
    def test_builtins_prefix(self):
        assert BUILTINS_PREFIX == "<builtins>."

    def test_unresolved_prefix(self):
        assert UNRESOLVED_PREFIX == "<unresolved>."


class TestBuiltinHelpers:
    def test_builtin_id_round_trips_with_builtin_name(self):
        assert builtin_id("len") == "<builtins>.len"
        assert builtin_name(builtin_id("len")) == "len"

    def test_is_builtin(self):
        assert is_builtin("<builtins>.print")
        assert not is_builtin("pkg.mod:print")
        assert not is_builtin("<unresolved>.print")
        assert not is_builtin("print")


class TestUnresolvedAttrHelpers:
    def test_unresolved_attr_id_round_trips_with_name(self):
        assert unresolved_attr_id("method") == "<unresolved>.method"
        assert unresolved_attr_name(unresolved_attr_id("method")) == "method"

    def test_is_unresolved_attr(self):
        assert is_unresolved_attr("<unresolved>.attr")
        assert not is_unresolved_attr("<builtins>.attr")
        assert not is_unresolved_attr("pkg.mod:Class.attr")
        assert not is_unresolved_attr("attr")


class TestModuleOf:
    def test_local_id(self):
        assert module_of("pkg.mod:f:x") == "pkg.mod"

    def test_scope_creator_id(self):
        assert module_of("pkg.mod:Class.method") == "pkg.mod"

    def test_bare_module_path_is_idempotent(self):
        assert module_of("pkg.mod") == "pkg.mod"
        assert module_of(module_of("pkg.mod:f:x")) == "pkg.mod"

    def test_only_first_colon_splits(self):
        assert module_of("m:f:x") == "m"


class TestLeafName:
    def test_unresolved_attr(self):
        assert leaf_name("<unresolved>.method") == "method"

    def test_builtin(self):
        assert leaf_name("<builtins>.len") == "len"

    def test_method_id(self):
        assert leaf_name("pkg.mod:Class.method") == "method"

    def test_local_id(self):
        assert leaf_name("pkg.mod:f:x") == "x"

    def test_module_level_local(self):
        assert leaf_name("mod:x") == "x"

    def test_bare_name(self):
        assert leaf_name("name") == "name"

    def test_class_field_id(self):
        assert leaf_name("mod:Class:field") == "field"

    def test_shadow_suffix_is_preserved(self):
        # leaf_name does not strip $N — that is strip_shadow's job.
        assert leaf_name("mod:f:x$2") == "x$2"


class TestShadowHandling:
    def test_strip_shadow_removes_suffix(self):
        assert strip_shadow("mod:f:x$2") == "mod:f:x"
        assert strip_shadow("mod:f:x$13") == "mod:f:x"

    def test_strip_shadow_no_suffix_unchanged(self):
        assert strip_shadow("mod:f:x") == "mod:f:x"

    def test_strip_shadow_non_numeric_dollar_unchanged(self):
        assert strip_shadow("mod:f:x$y") == "mod:f:x$y"

    def test_shadow_suffix_ordinal(self):
        assert shadow_suffix("mod:f:x$2") == 2
        assert shadow_suffix("mod:f:x$10") == 10

    def test_shadow_suffix_none_when_unshadowed(self):
        assert shadow_suffix("mod:f:x") is None
        assert shadow_suffix("mod:f:x$y") is None

    def test_shadow_id_round_trips_with_shadow_suffix(self):
        assert shadow_id("mod:f:x", 2) == "mod:f:x$2"
        assert shadow_suffix(shadow_id("mod:f:x", 3)) == 3
        assert strip_shadow(shadow_id("mod:f:x", 3)) == "mod:f:x"

    def test_round_trip_with_leaf_name(self):
        assert leaf_name(strip_shadow("pkg.mod:f:x$3")) == "x"


class TestModuleSymbolId:
    def test_returns_the_module_symbols_id(self, bind_source):
        index = bind_source("x = 1\n", file_path="pkg/mod.py")
        assert module_symbol_id(index) == "pkg.mod"

    def test_package_init_yields_package_path(self, bind_source):
        index = bind_source("x = 1\n", file_path="pkg/__init__.py")
        assert module_symbol_id(index) == "pkg"

    def test_none_when_no_module_symbol(self, bind_source):
        # A source root's own __init__.py maps to the empty module path, and
        # the binder emits no MODULE symbol for it.
        index = bind_source("x = 1\n", file_path="__init__.py")
        assert module_symbol_id(index) is None

    def test_none_for_an_empty_index(self):
        assert module_symbol_id(FileIndex(file_path="m.py", file_hash="", language="python")) is None
