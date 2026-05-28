"""Claude Code PostToolUse hook entry.

Invoked by Claude Code after every tool call. Reads the event JSON
from stdin, captures tool metadata (including mtime at read time for
file ops) into the session-scoped tool log.

Silent-fail discipline: never crash, never break the substrate.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from asof_core.hooks import post_tool


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw:
            return 0
        event = json.loads(raw)
        if not isinstance(event, dict):
            return 0

        tool_name = event.get("tool_name") or ""
        if not isinstance(tool_name, str) or not tool_name:
            return 0

        tool_input = event.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        session_id = event.get("session_id") or "unknown-session"

        tool_response = event.get("tool_response")
        if not isinstance(tool_response, dict):
            tool_response = None

        post_tool(
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            now=datetime.now(timezone.utc),
        )
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        # Silent failure — never break the substrate
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
