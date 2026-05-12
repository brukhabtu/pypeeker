"""Check engine: run enabled rules over every indexed file under config.src."""

from __future__ import annotations

from pypeeker.check.config import CheckConfig
from pypeeker.check.models import Violation
from pypeeker.check.rules import REGISTRY
from pypeeker.storage import IndexStore


class CheckEngine:
    """Glue between :class:`IndexStore` and the rule registry."""

    def __init__(self, store: IndexStore, config: CheckConfig) -> None:
        self._store = store
        self._config = config

    def run(self) -> list[Violation]:
        rules = [
            (name, REGISTRY[name])
            for name in self._config.rules
            if name in REGISTRY
        ]
        if not rules:
            return []

        src_prefixes = tuple(p.rstrip("/") + "/" for p in self._config.src)
        violations: list[Violation] = []
        for source_path in self._store.list_indexed_files():
            if src_prefixes and not any(
                source_path.startswith(prefix) for prefix in src_prefixes
            ):
                continue
            file_index = self._store.load(source_path)
            if file_index is None:
                continue
            for rule_name, rule in rules:
                options = self._config.rule_options.get(rule_name, {})
                violations.extend(rule(file_index, options))

        violations.sort()
        return violations
