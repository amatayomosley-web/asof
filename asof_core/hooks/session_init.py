"""Session-init hook.

Called once at session start (Claude Code SessionStart equivalent).
Detects the model ID, looks up training cutoff, emits the directive +
cutoff block. Initializes the session-scoped tool log.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import json
import os

from asof_core.cutoffs import lookup_cutoff, gap_to_now
from asof_core.output import render_session_init


def _file_annotation_enabled() -> bool:
    """Check config + env for the file-annotation toggle (default OFF)."""
    if os.environ.get("ASOF_FILE_ANNOTATION", "").lower() in ("on", "true", "1"):
        return True
    try:
        config_path = Path.home() / ".asof" / "config.json"
        if config_path.is_file():
            with config_path.open(encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("file_annotation") is True:
                return True
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return False


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

    result = render_session_init(current_dt=now, cutoff_gap=cutoff_gap)

    # Append file-annotation directive if enabled (opt-in via config or env)
    if _file_annotation_enabled():
        result += _file_annotation_directive()

    return result


def _file_annotation_directive() -> str:
    """The opt-in directive that teaches the agent to annotate inline
    as-of markers when writing files containing time-sensitive data.

    Without this directive, the agent writes normally. With it, the
    agent annotates dynamic data like `$890 [as-of: 2026-05-27]` so
    re-reads N days later catch precise per-datum staleness via the
    analyst parser.
    """
    return (
        "\n"
        "## File annotation directive (enabled)\n"
        "\n"
        "When writing files containing time-sensitive data (prices, rates,\n"
        "quotes, fetched facts, dated claims), annotate inline using an\n"
        "as-of marker appropriate to the file type:\n"
        "\n"
        "  Markdown / text:   $890 [as-of: 2026-05-27]\n"
        "  JSON:              \"price\": 890, \"_asof\": \"2026-05-27\"\n"
        "  YAML:              price: 890  # as-of: 2026-05-27\n"
        "  Source code:       # AsOf: 2026-05-27\n"
        "  Plain text:        $890 (as-of 2026-05-27)\n"
        "\n"
        "Annotate only data that can change over time. Don't annotate\n"
        "structural content, code logic, or things stable by nature.\n"
        "Match the file's existing convention where one exists. Use\n"
        "today's date in the marker.\n"
    )
