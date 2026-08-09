"""A star import, which `unused-imports` leaves to the `star-imports` rule.

The star binding's name is literally `*`; it binds nothing by name, so "unused"
is meaningless for it and the rule skips it explicitly rather than reporting a
finding no fix could act on.
"""

from flocal.helpers import *
