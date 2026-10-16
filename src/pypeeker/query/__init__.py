"""Semantic query engine: search symbols, references, and scopes across the indexed codebase."""

from pypeeker.query.engine import SemanticQueryEngine, SymbolTable
from pypeeker.query.match import symbol_matches

__all__ = ["SemanticQueryEngine", "SymbolTable", "symbol_matches"]
