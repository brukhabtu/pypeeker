"""Consumer code: two real `barrel-only` violations and every exemption.

* `helper` — imported at the barrel level. Clean: `target_module` IS the
  barrel module, so there is nothing deeper to complain about.
* `Engine` — VIOLATION. A deep import of a name `xf.core`'s curated barrel
  re-exports.
* `helper as core_helper` — VIOLATION, and the one that pins the wording: the
  message must quote the name the barrel exports (`helper`), not the local
  alias (`core_helper`).
* `internal_helper` — exempt. `xf.core`'s barrel does not re-export it.
* `Widget` — exempt. `xf.plain` declares no `__all__`, so it is uncurated.
* `sibling_value` — exempt. Same package as the importer.
"""

from xf.api.sibling import sibling_value
from xf.core import helper
from xf.core.engine import Engine
from xf.core.engine import helper as core_helper
from xf.core.internal import internal_helper
from xf.plain.widget import Widget


def build() -> str:
    """Use every imported name, so nothing here reads as an unused import."""
    return (
        helper()
        + core_helper()
        + Engine().run()
        + internal_helper()
        + str(Widget)
        + sibling_value()
    )
