"""An UNCURATED package: it re-exports, but declares no __all__.

The re-export alone is not the curation signal — `__all__` is. A deep import
of `Widget` from another package is therefore clean, even though this
`__init__` binds the same name.
"""

from xf.plain.widget import Widget
