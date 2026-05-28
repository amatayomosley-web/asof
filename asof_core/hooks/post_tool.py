"""PostToolUse hook.

Called after every tool call. Captures the tool's target and metadata
into the session-scoped tool log. Critical for the file-staleness
mechanism: captures mtime at read time (the "as-of marker").

Silent-fail discipline: never raise. The hook's job is to log; failure
to log is one missing record. Failure that crashes the substrate's tool
call is unacceptable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from asof_core.stat import stat_now


# Three-class volatility lookup. Matches the cairn-internal cairn_tool_log.py
# convention. First-pass coarse classification.
_VOLATILITY: dict[str, str] = {
    # session — substrate or this session controls state
    "Read": "session",
    "Glob": "session",
    "Grep": "session",
    "Edit": "session",
    "Write": "session",
    "MultiEdit": "session",
    "NotebookEdit": "session",
    "Agent": "session",
    "Task": "session",
    # external — state changes outside substrate awareness
    "Bash": "external",
    "PowerShell": "external",
    "WebFetch": "external",
    "WebSearch": "external",
    # static — stateless lookups (default fallback handles the rest)
    "ToolSearch": "static",
    "ScheduleWakeup": "static",
}


_FILE_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"})


def classify_tool(tool_name: str) -> str:
    """Return the volatility class for a tool name."""
    return _VOLATILITY.get(tool_name, "static")


def _summarize_input(tool_name: str, tool_input: dict) -> str:
    """Single-line summary of tool input, capped to ~300 chars."""
    if not isinstance(tool_input, dict):
        return ""
    candidates = [
        tool_input.get("file_path"),
        tool_input.get("path"),
        tool_input.get("pattern"),
        tool_input.get("query"),
        tool_input.get("url"),
        tool_input.get("command"),
        tool_input.get("description"),
    ]
    for c in candidates:
        if isinstance(c, str) and c:
            return c[:300]
    try:
        return json.dumps(tool_input)[:300]
    except (TypeError, ValueError):
        return ""


def post_tool(
    *,
    session_id: str,
    tool_name: str,
    tool_input: dict,
    log_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> None:
    """Append a tool-use record to the session-scoped tool log.

    Args:
        session_id: scope identifier; one log per session
        tool_name: the name of the tool that was invoked
        tool_input: structured input the tool received
        log_dir: directory for tool log files. Defaults to ~/.asof/tool_log/
        now: current datetime. Defaults to UTC now.
    """
    if log_dir is None:
        log_dir = Path.home() / ".asof" / "tool_log"
    if now is None:
        now = datetime.now(timezone.utc)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{session_id}.jsonl"

        record = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool_name": tool_name,
            "input_summary": _summarize_input(tool_name, tool_input),
            "volatility": classify_tool(tool_name),
        }

        # For file operations, capture mtime at read time — the as-of marker
        if tool_name in _FILE_TOOLS:
            file_path = tool_input.get("file_path") or tool_input.get("path") if isinstance(tool_input, dict) else None
            if file_path:
                stat = stat_now(file_path)
                if stat["exists"]:
                    record["mtime_at_read"] = stat["mtime_epoch"]
                    record["mtime_iso"] = stat["mtime_iso"]
                    record["size_bytes"] = stat["size_bytes"]

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        # Silent failure: never break the substrate's tool call
        pass
