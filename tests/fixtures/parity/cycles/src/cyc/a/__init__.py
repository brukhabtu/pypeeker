"""The package half of a colliding module id: this and ../a.py both bind `cyc.a`.

`cyc.a` is the module the a<->b cycle is charged to, so a port that keys the
cycle on the reporting module id and quantifies over the per-file `modules`
universe reports the cycle twice — the second time here, at a line copied from
a.py's import site.
"""

A = "a"
