"""Claude Code UserPromptSubmit hook entry.

Invoked by Claude Code before every model response. Reads the event
JSON (which includes the user's prompt text), runs the watch function,
emits the adaptive verdict block to stdout.

Empty stdout means no signal — Claude Code surfaces nothing.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from asof_core.hooks import watch

# Harness text, not a person's prompt. A task notification names its output file, so the watch reported on
# that path: 274 of 277 notifications, 98K chars, in the 2026-11 token audit. Same list as cairn_per_turn.py.
_MACHINE_PREFIXES = ("<task-notification>", "=== Transcript of", "<system-", "Caveat:")


def _is_machine_turn(prompt: str) -> bool:
    return prompt.lstrip().startswith(_MACHINE_PREFIXES)


def _load_config() -> dict:
    """Load AsOf config from ~/.asof/config.json. Returns {} if missing
    or invalid (default behavior takes over)."""
    try:
        config_path = Path.home() / ".asof" / "config.json"
        if not config_path.is_file():
            return {}
        with config_path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError, OSError):
        event = {}

    session_id = event.get("session_id") or "unknown-session"
    prompt_text = event.get("prompt") or ""
    if isinstance(prompt_text, list):
        # Multi-part prompts (Claude Code can send arrays of content blocks)
        parts = []
        for p in prompt_text:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text") or "")
            elif isinstance(p, str):
                parts.append(p)
        prompt_text = "\n".join(parts)

    if _is_machine_turn(prompt_text):
        return 0

    config = _load_config()

    try:
        out = watch(
            session_id=session_id,
            prompt_text=prompt_text,
            config=config,
            now=datetime.now(timezone.utc),
        )
        if out:
            print(out)
    except Exception:
        # Silent failure — never break the substrate
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
