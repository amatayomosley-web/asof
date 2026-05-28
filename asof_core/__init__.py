"""AsOf — temporal-awareness skill for tool-using LLMs.

A per-turn hook plus a teaching prose layer. Hook does the computation
in Python (so the model never does unreliable date arithmetic in chat).
Prose teaches the model how to interpret hook output.

Target substrates: Claude Code, Antigravity (Gemini), generic harnesses.

See: docs/design.md for the full specification.
"""
from __future__ import annotations

from asof_core.version import SCHEMA_VERSION, MIN_PROSE_VERSION

__all__ = ["SCHEMA_VERSION", "MIN_PROSE_VERSION"]
__version__ = "0.1.0"
