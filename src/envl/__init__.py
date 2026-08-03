"""envl: wrap fat command output in a compact, always-valid-JSON envelope.

The full output is cached as an immutable content-addressed blob outside the
repository; the envelope carries the shape, counts, loud truncation markers,
the blob path, and format-appropriate drill-in recipes. Nothing here imports
:mod:`pypeeker` — lifting this package into another project is a move, not a
rewrite.

Everything public in this package is re-exported here. A public module-level
symbol that is *not* in ``__all__`` is a mistake, not a style choice.
"""

from __future__ import annotations

from envl.cache import BlobCache, BlobRecord
from envl.capture import Capture, run_command
from envl.config import CommandRule, EnvelopeConfig, default_cache_dir, load_config
from envl.envelope import build_envelope, envelope_json, should_envelope
from envl.formats import Summary, Truncation, detect_format, summarize

__all__ = [
    "BlobCache",
    "BlobRecord",
    "Capture",
    "CommandRule",
    "EnvelopeConfig",
    "Summary",
    "Truncation",
    "build_envelope",
    "default_cache_dir",
    "detect_format",
    "envelope_json",
    "load_config",
    "run_command",
    "should_envelope",
    "summarize",
]
