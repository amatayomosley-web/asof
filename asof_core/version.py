"""Schema versioning for AsOf hook output.

Two coupled versions:

- SCHEMA_VERSION: the version of the verdict block format the hook emits.
  Bumped when fields are added, renamed, or semantics change.
- MIN_PROSE_VERSION: the minimum SCHEMA_VERSION that the bundled SKILL.md
  prose understands. Hook output emits its SCHEMA_VERSION; prose declares
  its compatibility floor; mismatch triggers an INCOMPATIBLE notice.

Addresses two failure modes from the adversarial review:

1. Hook/prose version skew (Elena): two-component design without enforced
   schema agreement. Hook adds a verdict type prose doesn't recognize, or
   prose teaches a pattern hook doesn't emit. Silent fallback to defaults.

2. Distribution version-skew permanence (Marcus): publicly-installed prose
   has no auto-update. Installed V1 prose can run against an updated hook
   in the field forever with no compatibility check. Loud failure at
   session-init is the fix.

Bump rules:
- Patch (0.0.X): bug fixes that don't change the output schema
- Minor (0.X.0): additive changes (new optional fields, new section types)
- Major (X.0.0): breaking changes (field renames, removed fields, semantic shifts)

Prose declares MIN_PROSE_VERSION as the SCHEMA_VERSION at which its
teaching was authored. As long as SCHEMA_VERSION >= MIN_PROSE_VERSION
and there are no breaking changes in between, the pairing is compatible.
"""
from __future__ import annotations

from typing import Optional

SCHEMA_VERSION = "0.1.0"
"""Hook output schema version. Emitted in every block."""

MIN_PROSE_VERSION = "0.1.0"
"""Minimum SCHEMA_VERSION the bundled prose understands."""


def parse_version(v: str) -> tuple[int, int, int]:
    """Parse a 'major.minor.patch' string into a tuple of ints."""
    parts = v.split(".")
    if len(parts) != 3:
        raise ValueError(f"version must be major.minor.patch, got {v!r}")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def is_compatible(hook_version: str, min_prose_version: str) -> bool:
    """Return True if a hook emitting `hook_version` is compatible with
    prose declaring `min_prose_version` as its floor.

    Compatibility rules:
    - Hook major must equal prose-min major (breaking changes require
      coordinated prose update)
    - Hook (minor, patch) must be >= prose-min (minor, patch)
    """
    h = parse_version(hook_version)
    p = parse_version(min_prose_version)
    if h[0] != p[0]:
        return False
    return h >= p


def incompatibility_notice(hook_version: str, prose_min_version: str) -> Optional[str]:
    """Return a loud INCOMPATIBLE block if a hook emitting `hook_version` does
    NOT satisfy prose declaring `prose_min_version` as its floor, else None.

    This is the runtime arm of the version guard. It catches the distribution
    version-skew permanence case (failure mode 2 above): publicly-installed
    prose has no auto-update, so an old installed SKILL can run against an
    upgraded hook in the field forever. Emitting this at session-init turns that
    silent skew into a loud, actionable notice. Malformed versions degrade to
    None (no false alarm).
    """
    try:
        if is_compatible(hook_version, prose_min_version):
            return None
    except (ValueError, TypeError):
        return None
    return (
        "\n## AsOf INCOMPATIBLE\n"
        f"  The installed AsOf skill prose declares min schema "
        f"{prose_min_version}, but the running hook emits schema {hook_version}. "
        "The prose may teach patterns this hook no longer emits (or vice versa) "
        "— verdicts could be misread. Re-install the skill to realign:\n"
        "    asof install --adapter claude_code\n"
    )
