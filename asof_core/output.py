"""Verdict block renderer for AsOf.

Composes the per-turn system-reminder block from active signals. Two
load-bearing principles:

1. **Adaptive rendering** — emit only when at least one section has an
   actionable signal. The whole point of the directive model is to be
   quiet when nothing matters, loud when something does. A turn with
   no triggers produces empty output (or just the version header).

2. **Pre-computed everything** — the model never does arithmetic in
   chat. Every gap, age, duration is rendered as a finished phrase
   ("4 months ago", "2 hours stale", "147 days") so the model reads
   the result rather than deriving it.

Section order (when present, top to bottom):
- Header (always when any section emits): schema version + time anchor
- ## Training cutoff (when initial session or on signal)
- ## File freshness — stale entries from working set
- ## Files referenced in your message
- ## Timestamps in your message
- ## Watchlist
- ## Time-sensitive phrasing detected
- ## Alert — warning summary
- Footer: short directive (session-start only)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from asof_core.version import SCHEMA_VERSION


def _format_header(*, current_dt: datetime, force: bool = False) -> list[str]:
    """Top-of-block header. Always includes schema version + current time."""
    local = current_dt.astimezone()
    lines = [
        f"=== AsOf v{SCHEMA_VERSION} ===",
        f"Now: {local.strftime('%a %Y-%m-%d %H:%M %Z')}",
    ]
    return lines


def _format_cutoff_section(cutoff_gap: dict) -> list[str]:
    """Training-cutoff awareness section. Only emit at session-init or
    when explicitly triggered."""
    return [
        "",
        "## Training cutoff",
        f"  Cutoff: {cutoff_gap.get('cutoff_str', 'unknown')} ({cutoff_gap.get('human', 'unknown gap')})",
        "  Hedge factual claims about state newer than the cutoff.",
    ]


def _format_file_freshness(stale_files: list[dict]) -> list[str]:
    """File-freshness section. Only emit when at least one file is stale.

    Args:
        stale_files: list of dicts with keys: path, drift_human, reason,
            verdict (should be "stale" for this section)
    """
    if not stale_files:
        return []
    lines = ["", "## File freshness (this session)"]
    for f in stale_files:
        lines.append(f"  STALE  {f['path']:50s}  {f.get('reason', 'mtime moved after read')}")
    return lines


def _format_path_mentions(mentioned: list[dict]) -> list[str]:
    """Path-mention section. Emit when prompt mentions paths that exist.

    Args:
        mentioned: list of dicts with keys: path, mtime_iso, age_human
    """
    if not mentioned:
        return []
    lines = ["", "## Files referenced in your message"]
    for m in mentioned:
        lines.append(f"  {m['path']:50s}  modified {m['age_human']}")
    return lines


def _format_timestamps(timestamps: list[dict]) -> list[str]:
    """Timestamps-in-prompt section. Pre-computed gap for each detected
    timestamp.

    Args:
        timestamps: output from timestamps.find_timestamps()
    """
    if not timestamps:
        return []
    lines = ["", "## Timestamps in your message"]
    for ts in timestamps:
        raw = ts["raw"]
        gap = ts["gap_human"]
        kind = ts.get("kind", "")
        line = f'  "{raw}"'.ljust(40) + f" → {gap}"
        if kind == "quarter":
            quarters = ts.get("quarter")
            year = ts.get("year")
            line += f"  (Q{quarters} {year}, announced ~mid-{ts['resolved'].strftime('%b %Y')})"
        lines.append(line)
    return lines


def _format_watchlist(watchlist_state: list[dict]) -> list[str]:
    """Watchlist section. Emit when any watchlist entry has changed
    since the previous check.

    Args:
        watchlist_state: list of dicts with keys: path, status, change_summary
    """
    if not watchlist_state:
        return []
    changed = [w for w in watchlist_state if w.get("changed")]
    if not changed:
        return []
    lines = ["", "## Watchlist"]
    for w in changed:
        lines.append(f"  {w['path']:50s}  {w.get('change_summary', 'changed')}")
    return lines


def _format_pattern_alerts(pattern_matches: list[dict]) -> list[str]:
    """Pattern-detection section. Emit a summary of time-sensitive
    phrasing detected in the user prompt.

    Args:
        pattern_matches: output from PatternMatcher.match_all()
    """
    if not pattern_matches:
        return []
    # Deduplicate by pattern name, keep first occurrence
    seen: set[str] = set()
    dedup: list[dict] = []
    for m in pattern_matches:
        if m["pattern"] not in seen:
            seen.add(m["pattern"])
            dedup.append(m)
    lines = ["", "## Time-sensitive phrasing detected"]
    for m in dedup[:6]:  # cap at 6 to avoid bloat
        tier_label = m["tier"]
        lines.append(f"  [{tier_label:8s}] {m['matched'][:60]}")
    if len(dedup) > 6:
        lines.append(f"  ({len(dedup) - 6} more)")
    return lines


def _format_alert_summary(stale_count: int, mode: str = "normal") -> list[str]:
    """Warning summary at end. Emit only in strict mode AND when stale
    files exist."""
    if stale_count == 0 or mode != "strict":
        return []
    return [
        "",
        f"WARNING: {stale_count} stale observation{'s' if stale_count != 1 else ''} above. "
        "Re-probe before grounding decisions on them.",
    ]


def _format_directive() -> list[str]:
    """Short directive included once at session-init (and as a footer
    reminder only on session-init turns)."""
    return [
        "",
        "Directive: When in-context data may be stale (files Read earlier, dated",
        "content in prompts, training-era facts), query asof_query for specifics",
        "rather than computing date math yourself. The hook pre-computes gaps;",
        "you apply the verdict.",
    ]


def render_session_init(*, current_dt: datetime, cutoff_gap: dict) -> str:
    """Render the session-start block. Always includes header, cutoff,
    and directive."""
    lines: list[str] = []
    lines.extend(_format_header(current_dt=current_dt, force=True))
    lines.extend(_format_cutoff_section(cutoff_gap))
    lines.extend(_format_directive())
    lines.append("")
    return "\n".join(lines)


def render_watch_block(
    *,
    current_dt: datetime,
    stale_files: Optional[list[dict]] = None,
    mentioned_paths: Optional[list[dict]] = None,
    timestamps: Optional[list[dict]] = None,
    watchlist_state: Optional[list[dict]] = None,
    pattern_matches: Optional[list[dict]] = None,
    mode: str = "normal",
) -> str:
    """Render the per-turn block. Returns empty string when no section
    has actionable content (adaptive rendering).

    Args:
        current_dt: current datetime (already in target TZ)
        stale_files: stale file verdicts from stat.classify_file_freshness
        mentioned_paths: path-mention results from stat.extract_paths_from_text
        timestamps: timestamp parse results from timestamps.find_timestamps
        watchlist_state: watchlist comparison results
        pattern_matches: pattern matcher results
        mode: "silent" | "normal" | "strict" — controls section verbosity
    """
    # Silent mode: suppress all per-turn output unconditionally
    if mode == "silent":
        return ""

    sections: list[list[str]] = []
    if stale_files:
        sections.append(_format_file_freshness(stale_files))
    if mentioned_paths:
        sections.append(_format_path_mentions(mentioned_paths))
    if timestamps:
        sections.append(_format_timestamps(timestamps))
    if watchlist_state:
        sections.append(_format_watchlist(watchlist_state))
    if pattern_matches:
        sections.append(_format_pattern_alerts(pattern_matches))

    # Adaptive rendering: empty output if nothing actionable
    has_content = any(s for s in sections)
    if not has_content:
        return ""

    out: list[str] = []
    out.extend(_format_header(current_dt=current_dt))
    for s in sections:
        out.extend(s)

    # Strict-mode summary
    stale_count = len(stale_files) if stale_files else 0
    out.extend(_format_alert_summary(stale_count, mode=mode))

    out.append("")
    out.append("=== end AsOf ===")
    return "\n".join(out)
