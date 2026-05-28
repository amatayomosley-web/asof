"""Watchlist — opt-in monitoring of paths regardless of substrate activity.

Config-declared file paths get stat'd every turn. State changes since
the previous check are surfaced. Useful for files the substrate doesn't
directly touch (orchestrator state, environment configs, lock files,
peer-edited collaboration documents).

Config format (~/.asof/watchlist.json):
    {
      "watch": [
        "~/project/state/active.json",
        "/var/lock/build.lock",
        "~/.env.production"
      ]
    }

Or simpler line-per-path format (~/.asof/watchlist):
    ~/project/state/active.json
    /var/lock/build.lock
    ~/.env.production

Per-session previous-mtime cache lives at
    ~/.asof/watchlist_cache/<session_id>.json
allowing the watch to detect "changed since this session's last check"
rather than just "exists at mtime X."
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from asof_core.stat import stat_now, format_duration


def load_watchlist() -> list[str]:
    """Read the user's watchlist. Returns paths as strings (NOT expanded —
    tilde expansion happens at stat time so it survives config relocation).
    """
    paths: list[str] = []

    # Plain-text watchlist (simple format)
    plain = Path.home() / ".asof" / "watchlist"
    if plain.is_file():
        try:
            with plain.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        paths.append(line)
        except OSError:
            pass

    # JSON watchlist (richer format, overrides plain if both exist)
    json_path = Path.home() / ".asof" / "watchlist.json"
    if json_path.is_file():
        try:
            with json_path.open(encoding="utf-8") as f:
                data = json.load(f)
            json_paths = data.get("watch", []) if isinstance(data, dict) else []
            if isinstance(json_paths, list):
                paths = [p for p in json_paths if isinstance(p, str) and p]
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    return paths


def _cache_path(session_id: str) -> Path:
    return Path.home() / ".asof" / "watchlist_cache" / f"{session_id}.json"


def _load_cache(session_id: str) -> dict[str, float]:
    """Load previous-mtime cache for this session. Returns dict mapping
    expanded-path → mtime_epoch."""
    p = _cache_path(session_id)
    if not p.is_file():
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_cache(session_id: str, cache: dict[str, float]) -> None:
    """Persist the cache. Silent on errors."""
    p = _cache_path(session_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(cache, f)
    except (OSError, TypeError, ValueError):
        pass


def evaluate_watchlist(*, session_id: str) -> list[dict]:
    """Stat each path in the watchlist, compare to cached mtimes, return
    entries that have changed since the previous check OR are newly seen.

    Returns:
        [
            {
                "path": expanded path,
                "changed": bool (True if mtime moved since cache or new entry),
                "change_summary": str — human-readable description,
                "current_mtime_iso": str or None,
                "age_human": str or None,
            },
            ...
        ]
    """
    paths = load_watchlist()
    if not paths:
        return []

    cache = _load_cache(session_id)
    new_cache: dict[str, float] = {}
    out: list[dict] = []
    now_epoch = datetime.now(timezone.utc).timestamp()

    for p in paths:
        # Expand ~ and env vars
        try:
            expanded = os.path.expandvars(str(Path(p).expanduser()))
        except (OSError, RuntimeError):
            continue
        s = stat_now(expanded)
        if not s["exists"]:
            # Was it cached as existing previously? If so, that's a change.
            if expanded in cache:
                out.append({
                    "path": expanded,
                    "changed": True,
                    "change_summary": "file no longer exists (was tracked at previous mtime)",
                    "current_mtime_iso": None,
                    "age_human": None,
                })
            continue

        current = s["mtime_epoch"]
        new_cache[expanded] = current

        previous = cache.get(expanded)
        age = now_epoch - current

        if previous is None:
            # First-time observation: not a "change" but still surface it
            out.append({
                "path": expanded,
                "changed": False,
                "change_summary": f"first observation this session ({format_duration(age)} ago)",
                "current_mtime_iso": s["mtime_iso"],
                "age_human": format_duration(age) + " ago",
            })
        elif abs(current - previous) > 2.0:  # tolerance from stat.MTIME_TOLERANCE_SECONDS
            drift = current - previous
            out.append({
                "path": expanded,
                "changed": True,
                "change_summary": f"modified {format_duration(abs(drift))} after last check",
                "current_mtime_iso": s["mtime_iso"],
                "age_human": format_duration(age) + " ago",
            })
        # Unchanged paths produce no output (adaptive rendering)

    _save_cache(session_id, new_cache)
    return out
