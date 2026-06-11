"""Auto-discovered builtin check rules.

Drop a module into this package and the check engine imports it before
resolving rule names, letting it self-register via
:func:`pypeeker.check.rules.register_rule` — no registry edits, no shared
files. This is how builtin rules are added; the dict literals in
``check/rules.py`` predate it and stay for the original rules.

Name precedence on clashes: the legacy ``REGISTRY``/``PROJECT_REGISTRY``
dicts in ``check/rules.py`` win over registered rules; among registered
rules the last import wins.

Import discipline: modules in this package must import from the concrete
modules (``pypeeker.check.rules``, ``pypeeker.check.models``,
``pypeeker.check.context``) — importing ``pypeeker.check`` itself from here
recurses into the engine import and creates a cycle.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def _import_submodules(package: ModuleType) -> list[str]:
    """Import every direct submodule of ``package``; return imported names.

    Exposed as a function (rather than inlined below) so the discovery
    mechanism is testable against an arbitrary package.
    """
    imported: list[str] = []
    for info in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{info.name}")
        imported.append(info.name)
    return imported


# A module may import itself once it exists in sys.modules; this avoids
# referencing __name__, which the binder doesn't yet resolve as a module
# global.
import pypeeker.check.builtin as _self

_import_submodules(_self)
