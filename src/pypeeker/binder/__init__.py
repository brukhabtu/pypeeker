"""Binder: walks tree-sitter CSTs into structured semantic models."""

from pypeeker.binder.binder import bind, visit_module, visit_node
from pypeeker.binder.state import BinderState

__all__ = ["BinderState", "bind", "visit_module", "visit_node"]
