"""Surfacing policy — decide WHEN to surface a true staleness verdict.

AsOf v0.1.0 broadcast: every turn re-listed every stale Read-file forever.
That habituates (warnings become wallpaper) and re-states zero-information
repeats. This module replaces the broadcast with:

- **First-surface: always**, on detection — the true fact is delivered once.
- **Suppress every-turn repeats** — re-stating a delivered, unchanged fact
  carries no new information.
- **Re-surface every HEARTBEAT_TURNS** while still stale AND still in the
  working set — a salience heartbeat against the model's recency-weighted
  attention (an old warning fades from salient context).
- **Re-surface immediately** on a new delta (mtime changed again) or
  re-access (the file's path mentioned this turn / Read again).
- **Stop** when the file leaves the working set (first-surface already
  delivered; the heartbeat goes quiet) or staleness resolves (re-read).

Recency governs *frequency* (how often to restate), never *truth* (the fact
is never hidden — first-surface always fires; the heartbeat only modulates
repetition while the file is still relevant).

State: ~/.asof/session_state/<session_id>.json
  {"turn": int, "last_watch_ts": float|null,
   "files": {path: {last_surfaced_turn, last_surfaced_mtime, last_access_turn}}}

See docs/staleness-surfacing-design.md for the full design + rejected
alternatives (broadcast, recency-as-suppression, pure on-access).
"""
from __future__ import annotations

__layer__ = "core"

import json
import os
from pathlib import Path
from typing import Optional


# Turns between heartbeat re-surfaces of a still-stale, still-relevant file.
# Floor matters: too small re-introduces habituation. ~12 keeps each re-surface
# a genuine re-alert rather than wallpaper.
DEFAULT_HEARTBEAT_TURNS = 12

# A file is "in the working set" if it was accessed within this many turns.
# Past this, the model has almost certainly moved on and won't reason from it,
# so the heartbeat goes quiet (first-surface was already delivered).
DEFAULT_WORKING_SET_TURNS = 15


def _config_int(env_key: str, cfg_key: str, default: int) -> int:
    """Resolve an int knob from env var, then ~/.asof/config.json, then default."""
    v = os.environ.get(env_key)
    if v:
        try:
            return int(v)
        except ValueError:
            pass
    try:
        cfg_path = Path.home() / ".asof" / "config.json"
        if cfg_path.is_file():
            with cfg_path.open(encoding="utf-8") as f:
                cfg = json.load(f)
            surf = cfg.get("surfacing", {}) or {}
            if isinstance(surf.get(cfg_key), int):
                return surf[cfg_key]
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return default


def heartbeat_turns() -> int:
    return _config_int("ASOF_HEARTBEAT_TURNS", "heartbeat_turns", DEFAULT_HEARTBEAT_TURNS)


def working_set_turns() -> int:
    return _config_int("ASOF_WORKING_SET_TURNS", "working_set_turns", DEFAULT_WORKING_SET_TURNS)


def _state_path(session_id: str, state_dir: Optional[Path]) -> Path:
    if state_dir is None:
        state_dir = Path.home() / ".asof" / "session_state"
    return state_dir / f"{session_id}.json"


def load_state(session_id: str, state_dir: Optional[Path] = None) -> dict:
    """Load surfacing state for a session; return a fresh skeleton on miss."""
    p = _state_path(session_id, state_dir)
    try:
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("turn", 0)
                d.setdefault("last_watch_ts", None)
                d.setdefault("files", {})
                return d
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"turn": 0, "last_watch_ts": None, "files": {}}


def save_state(session_id: str, state: dict, state_dir: Optional[Path] = None) -> None:
    """Persist surfacing state. Silent-fail — never break the hook."""
    p = _state_path(session_id, state_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(state, f)
    except (OSError, TypeError, ValueError):
        pass


def decide_surfacing(
    stale_files: list[dict],
    state: dict,
    current_turn: int,
    accessed_paths: set[str],
    *,
    heartbeat: Optional[int] = None,
    working_set: Optional[int] = None,
) -> list[dict]:
    """Filter the full stale list to the subset to SURFACE this turn, and
    update per-file surfacing memory in `state` in place.

    A stale file surfaces iff:
      - first time it's seen stale (first-surface, unconditional), OR
      - its mtime changed since last surfaced (new delta), OR
      - heartbeat is due (turns since last surface >= heartbeat) AND it's
        still in the working set (accessed within `working_set` turns).
    Otherwise it's suppressed (already delivered, nothing new).

    `accessed_paths`: paths Read or mentioned this turn — refreshes working-set
    membership.
    """
    if heartbeat is None:
        heartbeat = heartbeat_turns()
    if working_set is None:
        working_set = working_set_turns()

    files_state: dict = state.setdefault("files", {})

    # Refresh access recency for anything touched this turn.
    for p in accessed_paths:
        fs = files_state.setdefault(p, {})
        fs["last_access_turn"] = current_turn

    surfaced: list[dict] = []
    for f in stale_files:
        path = f["path"]
        cur_mtime = f.get("current_mtime")
        fs = files_state.setdefault(path, {})

        last_surfaced_turn = fs.get("last_surfaced_turn")
        last_surfaced_mtime = fs.get("last_surfaced_mtime")
        # First time we've ever seen this file stale → treat as accessed now.
        last_access_turn = fs.get("last_access_turn")
        if last_access_turn is None:
            last_access_turn = current_turn
            fs["last_access_turn"] = current_turn

        first_time = last_surfaced_turn is None
        new_delta = (
            last_surfaced_mtime is not None
            and cur_mtime is not None
            and cur_mtime != last_surfaced_mtime
        )
        heartbeat_due = (
            last_surfaced_turn is not None
            and (current_turn - last_surfaced_turn) >= heartbeat
        )
        in_working_set = (current_turn - last_access_turn) <= working_set

        if first_time or new_delta or (heartbeat_due and in_working_set):
            surfaced.append(f)
            fs["last_surfaced_turn"] = current_turn
            fs["last_surfaced_mtime"] = cur_mtime

    return surfaced
