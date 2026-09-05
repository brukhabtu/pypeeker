"""Semantic query engine: search symbols, references, and scopes across the indexed codebase."""

from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.query.match import symbol_matches

__all__ = ["SemanticQueryEngine", "symbol_matches"]
