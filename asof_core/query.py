"""asof_query — pull-based oracle for AsOf.

Model-callable tool. When the model judges that time matters but the
auto-push didn't surface specifics it needs, it can query the oracle
for a specific target (file path, URL, timestamp string, datum
description). All computation in Python.

Used by:
- The model, via the substrate's tool surface (registered per adapter)
- The CLI, for `asof query <target>` debugging
- Tests, for verifying oracle behavior

Returns structured verdict that downstream surfaces (system-reminder,
CLI output, JSON for programmatic callers) can render.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from asof_core.stat import stat_now, classify_file_freshness, format_duration
from asof_core.timestamps import find_timestamps, parse_iso, parse_quarter, humanize_gap
from asof_core.cutoffs import lookup_cutoff, gap_to_now


def query(target: str, *, kind_hint: Optional[str] = None, session_log: Optional[list[dict]] = None) -> dict:
    """Query the oracle for a freshness verdict on a target.

    Args:
        target: a path, URL, timestamp string, model ID, or generic
            text describing the datum
        kind_hint: optional hint to skip kind-detection ("file", "url",
            "timestamp", "model", "text")
        session_log: optional tool-log records for self-write detection

    Returns:
        {
            "kind": detected kind,
            "target": target string,
            "verdict": "fresh" | "stale" | "unverifiable" | "unknown",
            "reason": short string,
            "detail": kind-specific details (gap, mtime, ETag, etc.),
        }
    """
    target = target.strip()
    kind = kind_hint or _detect_kind(target)

    if kind == "file":
        return _query_file(target, session_log)
    if kind == "url":
        return _query_url(target)
    if kind == "timestamp":
        return _query_timestamp(target)
    if kind == "model":
        return _query_model(target)
    return _query_text(target)


def _detect_kind(target: str) -> str:
    """Heuristic kind detection for unknown-shape input."""
    # File-like: contains a path separator or is absolute
    if target.startswith("/") or target.startswith("~") or target.startswith("./") or target.startswith("../"):
        return "file"
    if len(target) > 2 and target[1] == ":" and target[2] in ("/", "\\"):
        return "file"
    # URL
    if target.startswith("http://") or target.startswith("https://"):
        return "url"
    # Model ID (heuristic — has a hyphen and starts with a known prefix)
    for prefix in ("claude-", "gemini-", "gpt-", "llama-", "mistral-"):
        if target.startswith(prefix):
            return "model"
    # Timestamp: looks like a date string
    if any(c.isdigit() for c in target):
        # Quick check for ISO/quarter/long-form
        if parse_iso(target):
            return "timestamp"
        if "Q" in target.upper() and any(c.isdigit() for c in target):
            return "timestamp"
        # Word-form date check below
    return "text"


def _query_file(path: str, session_log: Optional[list[dict]]) -> dict:
    """Stat the file and compare to any captured mtime_at_read in session log."""
    s = stat_now(path)
    if not s["exists"]:
        return {
            "kind": "file",
            "target": path,
            "verdict": "unverifiable",
            "reason": "file does not exist or unreadable",
            "detail": {},
        }

    # If we have a session log, find the most recent Read for this path
    mtime_at_read = None
    later_writes: list[float] = []
    if session_log:
        for r in session_log:
            if r.get("input_summary") == path:
                mtime = r.get("mtime_at_read")
                if not mtime:
                    continue
                if r.get("tool_name") == "Read":
                    if mtime_at_read is None or mtime > mtime_at_read:
                        mtime_at_read = mtime
                elif r.get("tool_name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                    later_writes.append(mtime)

    if mtime_at_read is None:
        # No prior Read in session — return raw mtime fact
        now_epoch = datetime.now(timezone.utc).timestamp()
        age = now_epoch - s["mtime_epoch"]
        return {
            "kind": "file",
            "target": path,
            "verdict": "unknown",
            "reason": "no prior Read in session; surfacing current mtime",
            "detail": {
                "mtime_iso": s["mtime_iso"],
                "age_human": format_duration(age) + " ago",
                "size_bytes": s["size_bytes"],
            },
        }

    # Compare and classify
    verdict = classify_file_freshness(path, mtime_at_read, later_self_writes=later_writes)
    return {
        "kind": "file",
        "target": path,
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
        "detail": {
            "mtime_at_read": mtime_at_read,
            "current_mtime": verdict.get("current_mtime"),
            "drift_seconds": verdict.get("drift_seconds"),
            "age_seconds": verdict.get("age_seconds"),
        },
    }


def _query_url(url: str, session_log: Optional[list[dict]] = None) -> dict:
    """URL freshness check via HEAD request.

    Compares current ETag/Last-Modified to values captured at the
    original WebFetch (recorded by post_tool when ASOF_URL_CAPTURE is on
    or config opts in).
    """
    from asof_core.url_freshness import classify_url_freshness

    # Find captured ETag/Last-Modified in session log
    etag_at_fetch: Optional[str] = None
    last_modified_at_fetch: Optional[str] = None
    if session_log:
        for r in session_log:
            if r.get("input_summary") == url and r.get("tool_name") in ("WebFetch", "WebSearch"):
                etag_at_fetch = r.get("etag_at_fetch") or etag_at_fetch
                last_modified_at_fetch = r.get("last_modified_at_fetch") or last_modified_at_fetch

    result = classify_url_freshness(
        url,
        etag_at_fetch=etag_at_fetch,
        last_modified_at_fetch=last_modified_at_fetch,
    )
    return {
        "kind": "url",
        "target": url,
        "verdict": result["verdict"],
        "reason": result["reason"],
        "detail": {
            "current_etag": result["current_etag"],
            "current_last_modified": result["current_last_modified"],
            "etag_at_fetch": etag_at_fetch,
            "last_modified_at_fetch": last_modified_at_fetch,
        },
    }


def _query_timestamp(text: str) -> dict:
    """Parse a timestamp string and return the gap to now."""
    now = datetime.now(timezone.utc).date()

    # Try ISO first
    d = parse_iso(text)
    if d:
        gap_days = (now - d).days
        return {
            "kind": "timestamp",
            "target": text,
            "verdict": "fresh" if gap_days <= 1 else "stale",
            "reason": f"resolved to {d.isoformat()}",
            "detail": {
                "resolved": d.isoformat(),
                "gap_days": gap_days,
                "gap_human": humanize_gap(gap_days),
            },
        }

    # Try quarter
    q = parse_quarter(text)
    if q:
        gap_days = (now - q["announce_date"]).days
        return {
            "kind": "timestamp",
            "target": text,
            "verdict": "stale" if gap_days > 90 else "fresh",
            "reason": f"resolved to Q{q['quarter']} {q['year']} (announced ~{q['announce_date'].isoformat()})",
            "detail": {
                "resolved": q["announce_date"].isoformat(),
                "year": q["year"],
                "quarter": q["quarter"],
                "gap_days": gap_days,
                "gap_human": humanize_gap(gap_days),
            },
        }

    # Fall back to find_timestamps full parser
    matches = find_timestamps(text)
    if matches:
        m = matches[0]
        return {
            "kind": "timestamp",
            "target": text,
            "verdict": "stale" if m["gap_days"] > 1 else "fresh",
            "reason": f"resolved to {m['resolved'].isoformat()} ({m['kind']})",
            "detail": {
                "resolved": m["resolved"].isoformat(),
                "gap_days": m["gap_days"],
                "gap_human": m["gap_human"],
            },
        }

    return {
        "kind": "timestamp",
        "target": text,
        "verdict": "unknown",
        "reason": "could not parse as a timestamp",
        "detail": {},
    }


def _query_model(model_id: str) -> dict:
    """Look up the model's training cutoff and compute gap."""
    cutoff = lookup_cutoff(model_id)
    if not cutoff or cutoff == "UNKNOWN-PENDING":
        return {
            "kind": "model",
            "target": model_id,
            "verdict": "unknown",
            "reason": f"no cutoff registered for {model_id}",
            "detail": {},
        }
    gap = gap_to_now(cutoff)
    return {
        "kind": "model",
        "target": model_id,
        "verdict": "stale" if gap["months"] >= 1 else "fresh",
        "reason": f"training cutoff {cutoff}",
        "detail": {
            "cutoff": cutoff,
            **gap,
        },
    }


def _query_text(text: str) -> dict:
    """Generic text query — try to extract any temporal info."""
    matches = find_timestamps(text)
    if matches:
        return {
            "kind": "text",
            "target": text,
            "verdict": "stale" if any(m["gap_days"] > 1 for m in matches) else "unknown",
            "reason": f"detected {len(matches)} timestamp reference(s)",
            "detail": {
                "timestamps": [
                    {
                        "raw": m["raw"],
                        "resolved": m["resolved"].isoformat(),
                        "gap_human": m["gap_human"],
                    }
                    for m in matches
                ],
            },
        }
    return {
        "kind": "text",
        "target": text,
        "verdict": "unknown",
        "reason": "no temporal references detected; freshness cannot be determined",
        "detail": {},
    }
