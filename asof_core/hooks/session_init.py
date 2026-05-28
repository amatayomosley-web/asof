"""Session-init hook.

Called once at session start (Claude Code SessionStart equivalent).
Detects the model ID, looks up training cutoff, emits the directive +
cutoff block. Initializes the session-scoped tool log.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from asof_core.cutoffs import lookup_cutoff, gap_to_now
from asof_core.output import render_session_init


def _ensure_tool_log_dir(log_dir: Path) -> None:
    """Create the tool log directory if missing. Silent on errors."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def session_init(
    *,
    model_id: Optional[str] = None,
    session_id: Optional[str] = None,
    log_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> str:
    """Emit the session-init block.

    Args:
        model_id: model identifier (e.g., "claude-opus-4-7"). If None,
            training-cutoff section is suppressed.
        session_id: session identifier — used to scope the tool log.
        log_dir: directory for tool log files. Defaults to ~/.asof/tool_log/
        now: current datetime. Defaults to UTC now.

    Returns:
        The rendered system-reminder text. Adapter writes this to stdout
        (or however the substrate ingests injection content).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if log_dir is None:
        log_dir = Path.home() / ".asof" / "tool_log"
    _ensure_tool_log_dir(log_dir)

    # Look up training cutoff (None for unknown models)
    cutoff_gap: dict = {}
    if model_id:
        cutoff = lookup_cutoff(model_id)
        if cutoff and cutoff != "UNKNOWN-PENDING":
            gap = gap_to_now(cutoff, now=now.date())
            cutoff_gap = {
                "cutoff_str": cutoff,
                **gap,
            }

    return render_session_init(current_dt=now, cutoff_gap=cutoff_gap)
