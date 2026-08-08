"""A module whose dotted name a package in the same tree also claims.

`app/dup/mod.py` and `app/dup/mod/__init__.py` both bind the module id
`app.dup.mod`, so the strict census must still report the unit `dup` exactly
once — at its representative, the __init__.
"""
