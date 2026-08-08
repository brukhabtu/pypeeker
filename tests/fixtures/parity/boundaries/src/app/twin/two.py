"""The other collision shape: one half violates and the other is permitted.

`app/twin/two.py` and `app/twin/two/__init__.py` share the module id
`app.twin.two` and both bind the local name `go`, so both bind
`app.twin.two:go`. Only this half violates; the package half imports something
`twin`'s allow-list permits. A verdict keyed on the shared id therefore charges
this file's violation to BOTH, and the extra finding names a package the other
file does not import at all.
"""

from app.thirdghost.z import go
