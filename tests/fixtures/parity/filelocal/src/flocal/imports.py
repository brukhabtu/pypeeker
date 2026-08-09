"""Import bindings for `unused-imports`: one real finding, and five exclusions.

`json` is the only binding this file yields a finding for. Every other import
is one of the rule's documented exclusions, present so a port that drops one
over-fires here rather than passing silently:

* `annotations` — a `__future__` import, which acts by existing;
* `_os` — an underscore-prefixed binding, the "imported for re-export / side
  effects" signal;
* `xml.etree.ElementTree` — a dotted import, which binds a namespace whose uses
  do not bind back to it;
* `Decimal` — used only inside a quoted annotation, invisible to reference
  analysis because the binder does not descend into string literals;
* `importlib` — genuinely used, the ordinary control.

The remaining exclusions need their own files: `__init__.py` (barrel),
`exported.py` (`__all__`), `star.py` (star import) and `dynamic.py` (the
`getattr` confidence downgrade).
"""

from __future__ import annotations

import importlib
import json
import os as _os
import xml.etree.ElementTree
from decimal import Decimal


def load(path):
    """Use `importlib`, so that binding is a used-import control."""
    return importlib.import_module(path)


def annotate(value: "Decimal") -> None:
    """Quote the annotation, so `Decimal` has no reference the binder can see."""
    del value
