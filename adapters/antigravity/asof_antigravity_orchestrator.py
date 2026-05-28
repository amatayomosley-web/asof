#!/usr/bin/env python
"""asof_antigravity_orchestrator.py

Orchestrator script for the AsOf temporal-awareness skill in Google Antigravity.
Synthesizes SessionStart, UserPromptSubmit, and PostToolUse events from PreInvocation.
"""
from __future__ import annotations

import os
import sys
import io
import json
import re
from pathlib import Path
from datetime import datetime, timezone

ASOF_DIR = Path.home() / ".asof"
TOOL_LOG_DIR = ASOF_DIR / "tool_log"
STATE_DIR = ASOF_DIR / "state"


# Training Cutoffs mapping (Standard Cutoff database fallback)
TRAINING_CUTOFFS = {
    "gemini-3.5-flash": "2025-10-01",
    "gemini-3.5-pro": "2025-10-01",
    "gemini-3.1-pro-preview": "2025-05-01",
    "default": "2025-10-01"
}

# Volatility classification lookup
VOLATILITY_MAP = {
    "view_file": "session",
    "read_file": "session",
    "list_dir": "session",
    "glob": "session",
    "grep_search": "session",
    "replace_file_content": "session",
    "write_to_file": "session",
    "multi_replace_file_content": "session",
    "run_command": "external",
    "search_web": "external",
    "read_url_content": "external",
    "invoke_subagent": "session"
}

# Regex Patterns for Tiers
TIER1_PATTERNS = [
    r"\b(current|latest|now|live)\s+(price|rate|version|cost|status|news|forecast|data|figures|quote|fare|value)\b",
    r"\bwhat'?s\s+(the\s+)?(current|latest|live)\b",
    r"\bis\s+\w+\s+still\s+(at|in|on|the\s+same|current|active|available|valid|live|open)\b",
    r"\bhas\s+\w+\s+changed\s+(since|in|after)\b",
    r"\b(stock|share)\s+(price|quote|value)\s+(of|for)\b",
    r"\b(real[-]?time)\s+\w+",
    r"\bup[-\s]?to[-\s]?date\b",
    r"\b(today'?s|yesterday'?s|this\s+(week|month|year)'?s)\s+(price|rate|data|figures|report|update|news)\b"
]

TIER2_PATTERNS = [
    r"\b(recently|lately|just\s+now)\b",
    r"\b(yesterday|today|tomorrow)\b",
    r"\b(last|next)\s+(week|month|year|quarter|day|hour|night|morning|afternoon|evening)\b",
    r"\b\d+\s+(days?|weeks?|months?|years?|hours?|minutes?)\s+ago\b",
    r"\b(this|next|last)\s+(month|quarter|year|fiscal\s+year)\b",
    r"\b(forecast|projection|estimate|prediction|outlook)\b",
    r"\b(deadline|expires?|expiring|due\s+date|cutoff)\b",
    r"\b(schedule|booking|reservation|appointment)\b",
    r"\b(when|how\s+long\s+ago)\b"
]

def _read_input() -> dict:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}

def get_last_processed_step(conv_id: str) -> int:
    state_file = STATE_DIR / f"state_{conv_id}.json"
    if not state_file.exists():
        return -1
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data.get("last_processed_step_index", -1)
    except Exception:
        return -1

def save_last_processed_step(conv_id: str, step_index: int):
    state_file = STATE_DIR / f"state_{conv_id}.json"
    try:
        state_file.write_text(json.dumps({"last_processed_step_index": step_index}), encoding="utf-8")
    except Exception:
        pass

def parse_tool_arg(tool_name: str, args: dict) -> str:
    """Extract a target description from tool arguments."""
    if not args:
        return ""
    if tool_name in ("view_file", "read_file", "write_to_file", "replace_file_content", "multi_replace_file_content"):
        return args.get("AbsolutePath", args.get("TargetFile", ""))
    if tool_name == "list_dir":
        return args.get("DirectoryPath", "")
    if tool_name == "run_command":
        return args.get("CommandLine", "")
    if tool_name == "read_url_content":
        return args.get("Url", "")
    if tool_name == "search_web":
        return args.get("query", "")
    return str(args)

def process_transcript_events(conv_id: str, transcript_path: str):
    """Scan transcript.jsonl for new completed tool calls and log them to tool_log."""
    if not transcript_path or not os.path.exists(transcript_path):
        return

    last_idx = get_last_processed_step(conv_id)
    max_idx = last_idx

    tool_log_file = TOOL_LOG_DIR / f"{conv_id}.jsonl"
    events_to_write = []

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    step_index = entry.get("step_index", -1)
                    if step_index <= last_idx:
                        continue
                    
                    if step_index > max_idx:
                        max_idx = step_index

                    # Detect tool usage in model responses
                    if entry.get("source") == "MODEL" and entry.get("type") == "PLANNER_RESPONSE":
                        tool_calls = entry.get("tool_calls", [])
                        for call in tool_calls:
                            name = call.get("name")
                            args = call.get("args", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    pass
                            target = parse_tool_arg(name, args)
                            volatility = VOLATILITY_MAP.get(name, "static")

                            log_entry = {
                                "ts": entry.get("created_at", datetime.now(timezone.utc).isoformat()),
                                "step_index": step_index,
                                "tool_name": name,
                                "target": target,
                                "volatility": volatility
                            }
                            events_to_write.append(log_entry)
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception:
        return

    if events_to_write:
        try:
            with open(tool_log_file, "a", encoding="utf-8") as out_f:
                for ev in events_to_write:
                    out_f.write(json.dumps(ev) + "\n")
        except Exception:
            pass

    if max_idx > last_idx:
        save_last_processed_step(conv_id, max_idx)

def get_latest_user_prompt(transcript_path: str) -> str:
    """Find the most recent user prompt in the transcript."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    
    prompts = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("source") == "USER_EXPLICIT" and entry.get("type") == "USER_INPUT":
                        prompts.append(entry.get("content", ""))
                except Exception:
                    continue
    except Exception:
        pass
    
    return prompts[-1] if prompts else ""

def check_file_freshness(conv_id: str) -> list[str]:
    """Check if any file read during the session is now stale on disk."""
    tool_log_file = TOOL_LOG_DIR / f"{conv_id}.jsonl"
    if not tool_log_file.exists():
        return []

    # Map file to the datetime it was last read
    file_reads: dict[str, datetime] = {}
    
    try:
        with open(tool_log_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    vol = ev.get("volatility")
                    target = ev.get("target")
                    if vol == "session" and target and os.path.isfile(target):
                        # Parse UTC timestamp
                        ts_str = ev.get("ts").replace("Z", "+00:00")
                        dt = datetime.fromisoformat(ts_str)
                        file_reads[target] = dt
                except Exception:
                    continue
    except Exception:
        return []

    stale_alerts = []
    for filepath, read_dt in file_reads.items():
        try:
            mtime = os.path.getmtime(filepath)
            mtime_dt = datetime.fromtimestamp(mtime, timezone.utc)
            if mtime_dt > read_dt:
                age_delta = datetime.now(timezone.utc) - read_dt
                hours = int(age_delta.total_seconds() // 3600)
                minutes = int((age_delta.total_seconds() % 3600) // 60)
                age_str = f"{hours}h{minutes}m" if hours > 0 else f"{minutes}m"
                
                mtime_delta = datetime.now(timezone.utc) - mtime_dt
                mtime_hours = int(mtime_delta.total_seconds() // 3600)
                mtime_minutes = int((mtime_delta.total_seconds() % 3600) // 60)
                mtime_str = f"{mtime_hours}h{mtime_minutes}m" if mtime_hours > 0 else f"{mtime_minutes}m"
                
                stale_alerts.append(
                    f"  STALE   {age_str}   {filepath}   (mtime moved {mtime_str} ago)"
                )
        except OSError:
            continue

    return stale_alerts

def parse_prompt_temporal_cues(prompt: str) -> list[str]:
    """Parse user prompt for time-sensitive phrasing using Tiers 1 and 2."""
    if not prompt:
        return []
    
    matched_phrases = []
    # Test Tier 1
    for pat in TIER1_PATTERNS:
        match = re.search(pat, prompt, re.IGNORECASE)
        if match:
            matched_phrases.append(f"High-Confidence: '{match.group(0)}'")
            
    # Test Tier 2
    for pat in TIER2_PATTERNS:
        match = re.search(pat, prompt, re.IGNORECASE)
        if match:
            matched_phrases.append(f"Medium-Confidence: '{match.group(0)}'")
            
    return matched_phrases

def main() -> int:
    # Ensure directories exist
    TOOL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    input_data = _read_input()
    conv_id = input_data.get("conversationId")
    invocation_num = input_data.get("invocationNum", 0)
    transcript_path = input_data.get("transcriptPath")

    if not conv_id:
        print(json.dumps({"injectSteps": []}))
        return 0

    # Parse and log new tool execution events if invocation_num > 0
    if invocation_num > 0 and transcript_path:
        process_transcript_events(conv_id, transcript_path)

    # State checks for session wake
    state_file = ASOF_DIR / "session_state.json"
    is_wake = False
    
    try:
        session_state = {}
        if state_file.exists():
            session_state = json.loads(state_file.read_text(encoding="utf-8"))
        if conv_id != session_state.get("last_conversation_id"):
            is_wake = True
            session_state["last_conversation_id"] = conv_id
            state_file.write_text(json.dumps(session_state), encoding="utf-8")
    except Exception:
        pass

    steps = []

    # 1. SessionStart (Wake) Injection
    if is_wake and invocation_num == 0:
        now_dt = datetime.now(timezone.utc)
        weekday = now_dt.strftime("%A")
        date_str = now_dt.strftime("%Y-%m-%d %H:%M")
        
        # Resolve model name to fetch cutoff
        # Example subset of env check
        model_name = os.environ.get("GEMINI_MODEL", "default")
        cutoff = TRAINING_CUTOFFS.get(model_name, TRAINING_CUTOFFS["default"])
        cutoff_dt = datetime.strptime(cutoff, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        gap = now_dt - cutoff_dt
        gap_days = gap.days
        gap_months = int(gap_days // 30.4)
        gap_str = f"~{gap_months} months" if gap_months > 0 else f"{gap_days} days"

        wake_msg = (
            f"=== AsOf v1.0.0 ===\n"
            f"Today: {weekday} {date_str} UTC\n"
            f"Training cutoff: {cutoff} ({gap_str} ago)\n\n"
            f"Directive: Consider time-decay when grounding claims. When in-context "
            f"data may be stale (files read earlier, dated content in prompts, "
            f"training-era facts), query asof_query for specifics rather than "
            f"computing date math yourself."
        )
        steps.append({"ephemeralMessage": wake_msg})

    # 2. UserPromptSubmit Injection (Invocation 0 only)
    if invocation_num == 0 and transcript_path:
        stale_files = check_file_freshness(conv_id)
        user_prompt = get_latest_user_prompt(transcript_path)
        temporal_cues = parse_prompt_temporal_cues(user_prompt)

        turn_blocks = []
        if stale_files:
            turn_blocks.append("## File freshness (this session)")
            turn_blocks.extend(stale_files)
            
        if temporal_cues:
            turn_blocks.append("\n## Time-sensitive phrasing detected")
            for cue in temporal_cues:
                turn_blocks.append(f"  {cue} → potential training cutoff skew.")

        if stale_files:
            turn_blocks.append("\n## Alert")
            turn_blocks.append(f"  WARNING: {len(stale_files)} files in working set are stale. Re-read before grounding.")

        if turn_blocks:
            prompt_alert = "=== AsOf Freshness Watch ===\n" + "\n".join(turn_blocks)
            steps.append({"ephemeralMessage": prompt_alert})

    print(json.dumps({"injectSteps": steps}))
    return 0

if __name__ == "__main__":
    # Force UTF-8 encoding
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
