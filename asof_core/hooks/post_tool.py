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
_URL_TOOLS = frozenset({"WebFetch", "WebSearch"})

# Synthetic tool_name used for self-write records that the watch hook's
# self-write index recognises. Emitted when an external-volatility tool
# (Bash, PowerShell, ...) writes a previously-Read file as a side effect.
SELF_WRITE_MARKER = "_asof_self_write"

# Execution-window tolerance (seconds). A PostToolUse hook fires at command
# completion; a file the command just wrote has mtime ~= completion time.
# A tracked file whose current mtime falls within this window of completion
# is attributed to the just-completed command (self-write), not an external
# editor. Kept tight: catches the common fast file-mutating commands
# (sed -i, >, tee, cp, mv, git checkout, formatters) whose write lands ~1s
# before the hook fires, while keeping the coincidental-external-write
# window small. Long-running commands that write a file many seconds before
# completing are NOT matched — the file stays conservatively flagged STALE
# (safe over-warn, never false silence).
CONCURRENT_WRITE_TOLERANCE = 5.0


def classify_tool(tool_name: str) -> str:
    """Return the volatility class for a tool name."""
    return _VOLATILITY.get(tool_name, "static")


def _tracked_read_paths(log_path: Path) -> list[str]:
    """Collect file paths the substrate Read this session, from the tool log.
    These are the files whose freshness the watch hook tracks; they're the
    candidates an external command might have written."""
    paths: list[str] = []
    seen: set[str] = set()
    try:
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if r.get("tool_name") == "Read":
                    p = r.get("input_summary")
                    if p and p not in seen:
                        seen.add(p)
                        paths.append(p)
    except OSError:
        pass
    return paths


def _detect_concurrent_self_writes(
    log_path: Path,
    now_epoch: float,
    tolerance: float = CONCURRENT_WRITE_TOLERANCE,
) -> list[tuple[str, float]]:
    """After an external-volatility tool completes at `now_epoch`, find
    previously-Read files whose current mtime is within `tolerance` of
    completion — i.e. the just-completed command wrote them.

    Execution-window invariant (anchored at completion): nothing external
    writes a file in lockstep with the completion of the agent's own
    command, so an aligned mtime means the command did it. Returns
    [(path, current_mtime_epoch)] for each such file."""
    out: list[tuple[str, float]] = []
    for path in _tracked_read_paths(log_path):
        s = stat_now(path)
        if s["exists"] and s["mtime_epoch"] is not None:
            if abs(now_epoch - s["mtime_epoch"]) <= tolerance:
                out.append((path, s["mtime_epoch"]))
    return out


def _url_capture_enabled() -> bool:
    """Check config + env for whether URL freshness capture is enabled.
    Off by default — network cost discipline."""
    import os
    if os.environ.get("ASOF_URL_CAPTURE", "").lower() in ("on", "true", "1"):
        return True
    try:
        config_path = Path.home() / ".asof" / "config.json"
        if config_path.is_file():
            with config_path.open(encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("patterns", {}).get("url_check") is True:
                return True
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return False


def _content_hash_enabled() -> bool:
    """Check config + env for whether read-time content hashing is enabled.
    Off by default — hashing reads the whole file (capped). Enables the
    no-op-write precision rung in classify_file_freshness."""
    import os
    if os.environ.get("ASOF_CONTENT_HASH", "").lower() in ("on", "true", "1"):
        return True
    try:
        config_path = Path.home() / ".asof" / "config.json"
        if config_path.is_file():
            with config_path.open(encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("precision", {}).get("hash") is True:
                return True
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return False


def _quote_datum_enabled() -> bool:
    """Check config + env for whether to capture a read-content excerpt.
    Off by default — adds a short snippet per Read to the tool log. When on,
    lets the watch hook co-locate a copy of the (possibly stale) datum with
    its STALE warning (display-only; never feeds the freshness verdict)."""
    import os
    if os.environ.get("ASOF_QUOTE_DATUM", "").lower() in ("on", "true", "1"):
        return True
    try:
        config_path = Path.home() / ".asof" / "config.json"
        if config_path.is_file():
            with config_path.open(encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("surfacing", {}).get("quote_datum") is True:
                return True
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return False


def _read_excerpt(tool_response: dict, max_len: int = 140) -> str:
    """A short, whitespace-collapsed snippet of a Read's returned content.
    Display-only — co-locates a copy of the datum with its staleness warning.
    Returns '' when no usable text content is present."""
    content = tool_response.get("content") if isinstance(tool_response, dict) else None
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("text") or b.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(b, str):
                parts.append(b)
        content = " ".join(parts)
    if not isinstance(content, str) or not content:
        return ""
    collapsed = " ".join(content.split())
    if len(collapsed) > max_len:
        return collapsed[:max_len].rstrip() + "…"
    return collapsed


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
    tool_response: Optional[dict] = None,
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
                    # Opt-in content-hash baseline (Class C precision). Off by
                    # default — hashing reads the whole file. When on, lets the
                    # watch hook tell a no-op write (identical content, moved
                    # mtime) from a real same-size change. Capped by size.
                    if tool_name == "Read" and _content_hash_enabled():
                        from asof_core.stat import content_hash
                        h = content_hash(file_path)
                        if h is not None:
                            record["hash_at_read"] = h
                    # Opt-in datum excerpt (surfacing co-location). Captures a
                    # snippet of what was actually read from tool_response so a
                    # copy of the datum can travel with its STALE warning.
                    if tool_name == "Read" and tool_response and _quote_datum_enabled():
                        excerpt = _read_excerpt(tool_response)
                        if excerpt:
                            record["read_excerpt"] = excerpt

        # For URL fetches, optionally capture ETag/Last-Modified via HEAD
        # (only fires when config opts in — network cost discipline)
        if tool_name in _URL_TOOLS:
            url = tool_input.get("url") if isinstance(tool_input, dict) else None
            if url and _url_capture_enabled():
                try:
                    from asof_core.url_freshness import head_request
                    h = head_request(url, timeout=2.0)
                    if h["ok"]:
                        if h["etag"]:
                            record["etag_at_fetch"] = h["etag"]
                        if h["last_modified"]:
                            record["last_modified_at_fetch"] = h["last_modified"]
                        if h["cache_control"]:
                            record["cache_control_at_fetch"] = h["cache_control"]
                except Exception:
                    pass

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # External-volatility tools (Bash, PowerShell) can mutate files
        # outside AsOf's Write/Edit tracking — sed -i, >, tee, cp, mv,
        # git checkout, formatters, build steps. Because this hook fires at
        # command completion, a file the command just wrote has mtime ~= now.
        # Record those as self-writes so the watch hook doesn't misattribute
        # the substrate's own Bash edits as external staleness.
        if classify_tool(tool_name) == "external":
            now_epoch = now.timestamp()
            for sw_path, sw_mtime in _detect_concurrent_self_writes(log_path, now_epoch):
                sw_record = {
                    "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "tool_name": SELF_WRITE_MARKER,
                    "input_summary": sw_path,
                    "mtime_at_read": sw_mtime,
                    "source_tool": tool_name,
                }
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(sw_record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        # Silent failure: never break the substrate's tool call
        pass
