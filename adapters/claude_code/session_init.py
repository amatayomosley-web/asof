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


def _find_model_from_transcript(session_id: str | None = None) -> str | None:
    """Return the *current* model ID for this session — the model on the LAST
    assistant message of this session's transcript.

    Two staleness traps this avoids (both observed: a 4-7 -> 4-8 substrate
    upgrade made AsOf report the stale 4-7 cutoff):
    - Wrong transcript: prefer the file named `<session_id>.jsonl` (Claude Code
      names transcripts by session id); only fall back to latest-by-mtime,
      which can be another concurrent session's file.
    - Stale model: a resumed or mid-session-upgraded transcript carries the OLD
      model on its early messages and the NEW one on recent messages — so take
      the LAST model seen, never the first.
    """
    try:
        proj_dir = Path.home() / ".claude" / "projects"
        if not proj_dir.is_dir():
            return None

        transcript = None
        if session_id:
            matches = list(proj_dir.glob(f"*/{session_id}.jsonl"))
            if matches:
                transcript = max(matches, key=lambda p: p.stat().st_mtime)
        if transcript is None:
            candidates = [j for d in proj_dir.iterdir() if d.is_dir()
                          for j in d.glob("*.jsonl")]
            if not candidates:
                return None
            transcript = max(candidates, key=lambda p: p.stat().st_mtime)

        latest_model: str | None = None
        with transcript.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = d.get("message") if isinstance(d.get("message"), dict) else None
                if msg and msg.get("model"):
                    latest_model = msg["model"]  # keep last, not first
        return latest_model
    except OSError:
        return None


def main() -> int:
    # Force UTF-8 stdout so non-ASCII survives the hook pipe. Done here, not at
    # import time, so importing this module (e.g. in tests) has no side effects.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
    event = _read_session_event()
    session_id = event.get("session_id") or "unknown-session"
    model_id = event.get("model") or _find_model_from_transcript(event.get("session_id"))

    out = session_init(
        model_id=model_id,
        session_id=session_id,
        now=datetime.now(timezone.utc),
    )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
