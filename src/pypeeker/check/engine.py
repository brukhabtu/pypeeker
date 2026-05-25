"""Check engine: run enabled rules over every indexed file under config.src."""

from __future__ import annotations

import importlib
import sys

from pypeeker.check.config import CheckConfig
from pypeeker.check.models import Violation
from pypeeker.check.rules import Rule, get_rule
from pypeeker.storage import IndexStore


class CheckConfigError(Exception):
    """Raised when check configuration (e.g. a plugin module) can't be loaded."""


class CheckEngine:
    """Glue between :class:`IndexStore` and the rule registry."""

    def __init__(self, store: IndexStore, config: CheckConfig) -> None:
        self._store = store
        self._config = config

    def run(self) -> list[Violation]:
        """Run every enabled rule over every indexed file under ``config.src``.

        Imports any configured plugin modules first (so their ``register_rule``
        decorators populate the registry), then runs each enabled rule.
        Returns violations sorted by (file_path, line, rule, message).
        """
        self._load_plugins()
        rules: list[tuple[str, Rule]] = []
        for name in self._config.rules:
            rule = get_rule(name)
            if rule is not None:
                rules.append((name, rule))
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

    def _load_plugins(self) -> None:
        """Import configured plugin modules so they register their rules.

        The project root is placed on ``sys.path`` so in-repo rule modules
        (e.g. a top-level ``lint_rules.py``) are importable, not just installed
        packages.
        """
        if not self._config.plugins:
            return
        root = str(self._store.project_root)
        added = root not in sys.path
        if added:
            sys.path.insert(0, root)
        try:
            for module in self._config.plugins:
                try:
                    importlib.import_module(module)
                except ImportError as exc:
                    raise CheckConfigError(
                        f"could not import check plugin '{module}': {exc}"
                    ) from exc
        finally:
            if added:
                sys.path.remove(root)
