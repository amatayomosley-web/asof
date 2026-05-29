"""AsOf MCP server — temporal-awareness oracle over the Model Context Protocol.

AsOf's host adapters (Claude Code, Antigravity) PUSH freshness verdicts via
lifecycle hooks. MCP is a request/response surface, so this adapter exposes
AsOf's PULL oracle instead: an MCP client (any MCP-compatible agent) calls
these tools on demand to anchor itself in real time and to check whether a
datum has gone stale.

What this DOESN'T do: the proactive per-turn surfacing / freshness heartbeat
the lifecycle hooks provide. MCP has no session tool-log, so a file target
reports its current mtime rather than a read-relative staleness verdict. For
the push model, use the Claude Code or Antigravity adapters.

Run as an MCP server over stdio (the default MCP transport):

    asof-mcp                       # installed console script
    python -m adapters.mcp.server  # from the repo

Register it in an MCP client's config, e.g.:

    {"mcpServers": {"asof": {"command": "asof-mcp"}}}

Requires the `mcp` extra:  pip install "asoftime[mcp]"
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP

from asof_core.query import query as _query
from asof_core.cutoffs import lookup_cutoff, gap_to_now

mcp = FastMCP("asof")


@mcp.tool()
def asof_now() -> dict:
    """Current real-world date and time (UTC).

    Call this to anchor reasoning in the present instead of a training-era
    sense of "now" — useful before any date math or recency judgement.
    """
    now = datetime.now(timezone.utc)
    return {
        "iso": now.isoformat(),
        "date": now.date().isoformat(),
        "weekday": now.strftime("%A"),
        "unix": now.timestamp(),
    }


@mcp.tool()
def asof_query(target: str, kind_hint: Optional[str] = None) -> dict:
    """Freshness oracle for a single datum.

    `target` may be a file path, URL, timestamp/date string, model ID, or free
    text. Returns a verdict — "fresh" | "stale" | "unverifiable" | "unknown" —
    with the computed time gap and kind-specific detail. `kind_hint` optionally
    forces detection: "file" | "url" | "timestamp" | "model" | "text".

    Note: with no session tool-log, a file target reports current mtime rather
    than a read-relative staleness verdict (that needs the push-hook adapters).
    """
    return _query(target, kind_hint=kind_hint)


@mcp.tool()
def asof_cutoff(model_id: str) -> dict:
    """Training-cutoff staleness for a model ID (e.g. "claude-opus-4-8").

    Returns the registered training cutoff and how long ago it was, so the
    caller knows how stale its parametric knowledge may be. Returns
    cutoff=None when the model is not in AsOf's registry.
    """
    cutoff = lookup_cutoff(model_id)
    if not cutoff or cutoff == "UNKNOWN-PENDING":
        return {"model": model_id, "cutoff": None,
                "reason": "no cutoff registered for this model"}
    return {"model": model_id, "cutoff": cutoff, **gap_to_now(cutoff)}


def main() -> None:
    """Run the AsOf MCP server over stdio (the default MCP transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
