"""Claude Code SessionStart hook entry.

Invoked by Claude Code at session begin. Reads session metadata from
the JSONL transcript, looks up model cutoff, emits the directive block
to stdout (which Claude Code injects as a system-reminder).
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout so non-ASCII characters survive the hook pipe
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from asof_core.hooks import session_init


def _read_session_event() -> dict:
    """Read the session event JSON from stdin. Returns {} on failure."""
    try:
        raw = sys.stdin.read()
        if not raw:
            return {}
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def _find_model_from_transcript() -> str | None:
    """Walk the latest session JSONL and return the model ID from any
    message. Claude Code records the model in every assistant message."""
    try:
        proj_dir = Path.home() / ".claude" / "projects"
        # Project dirs have hashed names; find the latest by mtime
        candidates = []
        if proj_dir.is_dir():
            for d in proj_dir.iterdir():
                if d.is_dir():
                    jsonls = list(d.glob("*.jsonl"))
                    if jsonls:
                        candidates.extend(jsonls)
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        with latest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = d.get("message") if isinstance(d.get("message"), dict) else None
                if msg and msg.get("model"):
                    return msg["model"]
        return None
    except OSError:
        return None


def main() -> int:
    event = _read_session_event()
    session_id = event.get("session_id") or "unknown-session"
    model_id = event.get("model") or _find_model_from_transcript()

    out = session_init(
        model_id=model_id,
        session_id=session_id,
        now=datetime.now(timezone.utc),
    )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
