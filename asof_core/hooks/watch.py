"""UserPromptSubmit watch hook.

Called once per user message. Reads the session-scoped tool log, stats
current filesystem state, parses the user prompt, applies pattern
matching. Produces the adaptive verdict block.

This is the load-bearing per-turn output. Most turns produce empty
output (adaptive rendering — silent when no signal). Turns that have a
stale file, a dated mention, a path reference, or matched time-sensitive
phrasing produce a structured block.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from asof_core.stat import (
    stat_now,
    classify_file_freshness,
    extract_paths_from_text,
    format_duration,
)
from asof_core.timestamps import find_timestamps
from asof_core.patterns import PatternMatcher
from asof_core.output import render_watch_block
from asof_core.watchlist import evaluate_watchlist


def _load_tool_log(log_path: Path) -> list[dict]:
    """Read a tool log into a list of records."""
    records: list[dict] = []
    if not log_path.is_file():
        return records
    try:
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        records.append(d)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return records


def _build_self_writes_index(records: list[dict]) -> dict[str, list[float]]:
    """For each file path the substrate wrote to, collect the write epochs.
    Used by classify_file_freshness to exclude self-induced mtime changes
    from the stale verdict.

    Two sources of self-writes:
    - Native write tools (Write/Edit/MultiEdit/NotebookEdit): the recorded
      mtime_at_read is the post-write mtime.
    - SELF_WRITE_MARKER records: emitted by post_tool when an external tool
      (Bash, PowerShell) wrote a previously-Read file concurrently with the
      command's completion (sed -i, >, cp, git checkout, formatters, ...).
      Without these, Bash file edits are misattributed as external staleness.
    """
    from asof_core.hooks.post_tool import _FILE_TOOLS, SELF_WRITE_MARKER
    writes: dict[str, list[float]] = {}
    for r in records:
        tool = r.get("tool_name")
        is_native_write = tool in _FILE_TOOLS and tool != "Read"
        is_bash_self_write = tool == SELF_WRITE_MARKER
        if is_native_write or is_bash_self_write:
            target = r.get("input_summary") or ""
            mtime = r.get("mtime_at_read")
            if target and mtime:
                writes.setdefault(target, []).append(mtime)
    return writes


def _evaluate_working_set(records: list[dict]) -> list[dict]:
    """Walk the tool log, compute current freshness for each Read entry.
    Returns only stale verdicts (per adaptive rendering — fresh files
    don't get surfaced)."""
    self_writes = _build_self_writes_index(records)
    stale: list[dict] = []
    for r in records:
        if r.get("tool_name") != "Read":
            continue
        path = r.get("input_summary") or ""
        mtime_at_read = r.get("mtime_at_read")
        if not path or mtime_at_read is None:
            continue

        verdict = classify_file_freshness(
            path,
            mtime_at_read,
            later_self_writes=self_writes.get(path, []),
        )
        if verdict["verdict"] == "stale":
            drift_s = verdict.get("drift_seconds", 0)
            stale.append({
                "path": path,
                "drift_human": format_duration(drift_s) if drift_s else "",
                "reason": verdict["reason"],
                "verdict": "stale",
                "current_mtime": verdict.get("current_mtime"),
            })
    return stale


def _accessed_paths_this_turn(records: list[dict], mentioned: list[dict],
                              last_watch_ts: Optional[float]) -> set[str]:
    """Paths the substrate touched since the previous watch fire — Reads
    logged after last_watch_ts, plus paths mentioned in this turn's prompt.
    Used to refresh working-set membership for the heartbeat decision."""
    from datetime import datetime, timezone as _tz
    accessed: set[str] = set()
    for m in mentioned:
        p = m.get("path")
        if p:
            accessed.add(p)
    if last_watch_ts is None:
        return accessed
    for r in records:
        if r.get("tool_name") != "Read":
            continue
        ts = r.get("ts")
        if not ts:
            continue
        try:
            epoch = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            continue
        if epoch >= last_watch_ts:
            p = r.get("input_summary")
            if p:
                accessed.add(p)
    return accessed


def _evaluate_path_mentions(text: str) -> list[dict]:
    """Find path-like strings in `text`, stat each that exists, surface
    mtime. Skips paths that don't resolve."""
    paths = extract_paths_from_text(text)
    out: list[dict] = []
    now_epoch = datetime.now(timezone.utc).timestamp()
    seen: set[str] = set()
    for p in paths:
        # Normalize ~/ paths
        try:
            resolved = str(Path(p).expanduser())
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        s = stat_now(resolved)
        if not s["exists"]:
            continue
        age = now_epoch - s["mtime_epoch"]
        out.append({
            "path": resolved,
            "mtime_iso": s["mtime_iso"],
            "age_human": format_duration(age) + " ago",
        })
    return out


def watch(
    *,
    session_id: str,
    prompt_text: str = "",
    log_dir: Optional[Path] = None,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> str:
    """Produce the per-turn verdict block.

    Args:
        session_id: scope identifier for the tool log
        prompt_text: the user's current prompt — parsed for timestamps,
            paths, time-sensitive phrasing
        log_dir: directory for tool log files. Defaults to ~/.asof/tool_log/
        config: user config dict. Supports:
            - patterns.high_confidence: bool (default True)
            - patterns.medium_confidence: bool (default True)
            - patterns.domains: list[str] (default [])
            - mode: "silent" | "normal" | "strict" (default "normal")
        now: current datetime. Defaults to UTC now.

    Returns:
        Verdict block string. Empty when no actionable signal (adaptive
        rendering).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if log_dir is None:
        log_dir = Path.home() / ".asof" / "tool_log"
    if config is None:
        config = {}

    mode = config.get("mode", "normal")

    log_path = log_dir / f"{session_id}.jsonl"
    records = _load_tool_log(log_path)

    # File-freshness verdicts (stale only — adaptive rendering)
    stale_files = _evaluate_working_set(records)

    # Path mentions in the prompt
    mentioned = _evaluate_path_mentions(prompt_text) if prompt_text else []

    # Surfacing policy: don't broadcast every stale file every turn. Surface
    # once, suppress repeats, re-surface on a heartbeat (while still in the
    # working set) or a new delta. State persists in ~/.asof/session_state/.
    from asof_core import surfacing
    surf_state = surfacing.load_state(session_id)
    current_turn = surf_state.get("turn", 0) + 1
    surf_state["turn"] = current_turn
    if stale_files:
        accessed = _accessed_paths_this_turn(
            records, mentioned, surf_state.get("last_watch_ts")
        )
        stale_files = surfacing.decide_surfacing(
            stale_files, surf_state, current_turn, accessed
        )
    surf_state["last_watch_ts"] = now.timestamp()
    surfacing.save_state(session_id, surf_state)

    # Timestamps in the prompt
    timestamps = find_timestamps(prompt_text, base_date=now.date()) if prompt_text else []

    # Pattern-based time-sensitive phrasing
    pattern_cfg = config.get("patterns", {}) or {}
    matcher = PatternMatcher(
        high_confidence=pattern_cfg.get("high_confidence", True),
        medium_confidence=pattern_cfg.get("medium_confidence", True),
        domains=pattern_cfg.get("domains", []),
    )
    pattern_matches = matcher.match_all(prompt_text) if prompt_text else []

    # Watchlist evaluation — only changed entries surface
    watchlist_state = evaluate_watchlist(session_id=session_id)

    return render_watch_block(
        current_dt=now,
        stale_files=stale_files,
        mentioned_paths=mentioned,
        timestamps=timestamps,
        watchlist_state=watchlist_state,
        pattern_matches=pattern_matches,
        mode=mode,
    )
