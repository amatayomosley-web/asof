"""Filesystem stat helpers for AsOf.

The load-bearing primitive of the file-staleness case. Captures mtime at
PostToolUse time (the "as-of marker") and compares to current mtime at
UserPromptSubmit time (the "now check") to produce freshness verdicts.

The conditional-staleness model (see docs/design.md §3): a datum is stale
only if (a) something could have changed it AND (b) we cannot rule out
that it did. mtime comparison handles both halves at once for files:
- mtime unchanged → nothing changed → fresh
- mtime moved → something changed → who? check if substrate's own
  Edit/Write to same path explains it (self-write); if not → stale.

All operations silent-fail. The skill must never break the substrate's
flow to keep its log clean.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Tolerance for filesystem timestamp granularity. FAT32 has 2-second mtime
# resolution; NFS has clock-skew issues; some network filesystems lag.
# A 2-second tolerance prevents false-stale verdicts on these systems.
MTIME_TOLERANCE_SECONDS = 2.0

# Substrate write tools (Claude Code names; adapters override if needed).
WRITE_TOOL_NAMES = frozenset({
    "Edit", "Write", "MultiEdit", "NotebookEdit",
})


def get_mtime(path: str | Path) -> Optional[float]:
    """Return the file's modification time as a Unix epoch float, or None
    if the file doesn't exist or can't be stat'd."""
    try:
        return os.path.getmtime(str(path))
    except (OSError, ValueError):
        return None


def stat_now(path: str | Path) -> dict:
    """Stat a path and return a dict suitable for embedding in the tool log.

    Returns:
        {
            "exists": bool,
            "mtime_epoch": float or None,
            "mtime_iso": ISO 8601 UTC string or None,
            "size_bytes": int or None,
            "is_symlink": bool,
        }
    """
    p = Path(path)
    if not p.exists():
        return {
            "exists": False,
            "mtime_epoch": None,
            "mtime_iso": None,
            "size_bytes": None,
            "is_symlink": False,
        }
    try:
        st = p.lstat()  # lstat to detect symlinks
        is_symlink = p.is_symlink()
        if is_symlink:
            # Resolve and re-stat for the actual content's mtime
            try:
                target_st = p.resolve(strict=True).stat()
                mtime = target_st.st_mtime
                size = target_st.st_size
            except (OSError, RuntimeError):
                mtime = st.st_mtime
                size = st.st_size
        else:
            mtime = st.st_mtime
            size = st.st_size
        return {
            "exists": True,
            "mtime_epoch": mtime,
            "mtime_iso": datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size_bytes": size,
            "is_symlink": is_symlink,
        }
    except OSError:
        return {
            "exists": False,
            "mtime_epoch": None,
            "mtime_iso": None,
            "size_bytes": None,
            "is_symlink": False,
        }


def classify_file_freshness(
    path: str | Path,
    mtime_at_read: float,
    *,
    later_self_writes: Optional[list[float]] = None,
    tolerance: float = MTIME_TOLERANCE_SECONDS,
) -> dict:
    """Compare the file's current mtime to the mtime captured at read time.

    The conditional-staleness rule:
    - current_mtime <= mtime_at_read + tolerance: fresh (file unchanged)
    - current_mtime > mtime_at_read AND a substrate write at a later epoch
      matches the current mtime (within tolerance): fresh (self-write)
    - current_mtime > mtime_at_read AND no matching self-write: stale
    - file doesn't exist now: unverifiable

    Args:
        path: file path to check
        mtime_at_read: the mtime captured by PostToolUse when the substrate
            originally Read this file
        later_self_writes: optional list of epoch timestamps when the
            substrate itself wrote to this path AFTER the original Read.
            Used to override stale verdicts for self-induced mtime changes.
        tolerance: seconds of slack for filesystem timestamp granularity

    Returns:
        {
            "verdict": "fresh" | "stale" | "unverifiable",
            "reason": short string,
            "current_mtime": float or None,
            "age_seconds": float or None (since the original Read),
            "drift_seconds": float or None (current_mtime - mtime_at_read),
        }
    """
    s = stat_now(path)
    if not s["exists"]:
        return {
            "verdict": "unverifiable",
            "reason": "file does not exist or unreadable",
            "current_mtime": None,
            "age_seconds": None,
            "drift_seconds": None,
        }

    current_mtime = s["mtime_epoch"]
    now_epoch = datetime.now(timezone.utc).timestamp()
    age = now_epoch - mtime_at_read
    drift = current_mtime - mtime_at_read

    # mtime unchanged within tolerance → fresh
    if drift <= tolerance:
        return {
            "verdict": "fresh",
            "reason": "mtime unchanged since read",
            "current_mtime": current_mtime,
            "age_seconds": age,
            "drift_seconds": drift,
        }

    # mtime moved — check if a substrate self-write explains it
    if later_self_writes:
        for write_epoch in later_self_writes:
            if abs(current_mtime - write_epoch) <= tolerance:
                return {
                    "verdict": "fresh",
                    "reason": f"mtime change explained by substrate write at {datetime.fromtimestamp(write_epoch, tz=timezone.utc).isoformat()}",
                    "current_mtime": current_mtime,
                    "age_seconds": age,
                    "drift_seconds": drift,
                }

    return {
        "verdict": "stale",
        "reason": f"mtime moved {format_drift(drift)} after read, no matching self-write",
        "current_mtime": current_mtime,
        "age_seconds": age,
        "drift_seconds": drift,
    }


def format_drift(seconds: float) -> str:
    """Render a drift value (seconds since the read) in compact human form."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    hours = seconds / 3600
    if hours < 24:
        h = int(hours)
        m = int((seconds - h * 3600) / 60)
        return f"{h}h{m}m" if m else f"{h}h"
    days = int(hours / 24)
    return f"{days}d{int(hours) % 24}h"


def format_duration(seconds: float) -> str:
    """Alias for format_drift, used for any duration rendering."""
    return format_drift(seconds)


def extract_paths_from_text(text: str) -> list[str]:
    """Extract path-like strings from text. Returns paths that look
    filesystem-like; caller checks existence via stat.

    Detected forms:
    - Absolute Unix paths: /foo/bar.py
    - Absolute Windows paths: C:/foo/bar.py or C:\\foo\\bar.py
    - Relative paths with extension: src/auth.py, ./config.yaml
    - Tilde-prefixed: ~/notes.md, ~/.config/file.json
    - Backtick-quoted: `auth.py`

    False positives are acceptable — the stat call filters non-existent
    paths. The watch suppresses paths that don't resolve.
    """
    import re
    patterns = [
        # Absolute Unix paths (must have at least one /char/ after root)
        r"/[\w./\\-]+\.[a-zA-Z0-9]{1,8}\b",
        # Windows paths with drive letter
        r"[A-Za-z]:[\\/][\w./\\-]+\.[a-zA-Z0-9]{1,8}\b",
        # Tilde-prefixed
        r"~/[\w./\\-]+(\.[a-zA-Z0-9]{1,8})?\b",
        # Backtick-quoted file names (any string with an extension)
        r"`([\w./\\-]+\.[a-zA-Z0-9]{1,8})`",
        # Relative with explicit ./ or ../
        r"\.\.?/[\w./\\-]+\.[a-zA-Z0-9]{1,8}\b",
    ]
    found: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in re.finditer(pat, text):
            candidate = m.group(1) if m.lastindex else m.group(0)
            candidate = candidate.strip("`'\"")
            if candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
    return found
