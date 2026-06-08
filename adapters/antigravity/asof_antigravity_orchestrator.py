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
from pathlib import Path
from datetime import datetime, timezone

# Add the parent directory (asof project root) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from asof_core.hooks import session_init, post_tool, watch

ASOF_DIR = Path.home() / ".asof"
TOOL_LOG_DIR = ASOF_DIR / "tool_log"
STATE_DIR = ASOF_DIR / "state"

AG_TOOL_MAP = {
    "view_file": "Read",
    "read_file": "Read",
    "list_dir": "Glob",
    "grep_search": "Grep",
    "replace_file_content": "Edit",
    "write_to_file": "Write",
    "multi_replace_file_content": "MultiEdit",
    "run_command": "Bash",
    "search_web": "WebSearch",
    "read_url_content": "WebFetch"
}

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
    tmp_file = STATE_DIR / f"state_{conv_id}.json.tmp"
    try:
        tmp_file.write_text(json.dumps({"last_processed_step_index": step_index}), encoding="utf-8")
        os.replace(tmp_file, state_file)
    except Exception:
        pass

def map_to_core_tool(ag_name: str, ag_args: dict) -> tuple[str, dict]:
    name = AG_TOOL_MAP.get(ag_name, ag_name)
    input_dict = dict(ag_args)
    if name in ("Read", "Edit", "Write", "MultiEdit"):
        target = ag_args.get("AbsolutePath") or ag_args.get("TargetFile") or ""
        input_dict["file_path"] = target
    elif name == "Bash":
        input_dict["command"] = ag_args.get("CommandLine", "")
    elif name == "WebFetch":
        input_dict["url"] = ag_args.get("Url", "")
    elif name == "WebSearch":
        input_dict["query"] = ag_args.get("query", "")
    elif name == "Glob":
        input_dict["path"] = ag_args.get("DirectoryPath", "")
    return name, input_dict

def process_transcript_events(conv_id: str, transcript_path: str):
    """Scan transcript.jsonl for new completed tool calls and pass them to post_tool."""
    if not transcript_path or not os.path.exists(transcript_path):
        return

    last_idx = get_last_processed_step(conv_id)
    max_idx = last_idx

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
                        
                        # Get the timestamp for when the tool call was made
                        ts_str = entry.get("created_at", datetime.now(timezone.utc).isoformat())
                        ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        
                        for call in tool_calls:
                            ag_name = call.get("name")
                            args = call.get("args", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    pass
                                    
                            core_name, core_input = map_to_core_tool(ag_name, args)
                            
                            post_tool(
                                session_id=conv_id,
                                tool_name=core_name,
                                tool_input=core_input,
                                now=ts_dt,
                                log_dir=TOOL_LOG_DIR
                            )
                except (json.JSONDecodeError, TypeError):
                    continue
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

def main() -> int:
    # Ensure directories exist
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
            tmp_state = ASOF_DIR / "session_state.json.tmp"
            tmp_state.write_text(json.dumps(session_state), encoding="utf-8")
            os.replace(tmp_state, state_file)
    except Exception:
        pass

    steps = []

    # 1. SessionStart (Wake) Injection
    if is_wake and invocation_num == 0:
        model_name = os.environ.get("GEMINI_MODEL", "default")
        wake_msg = session_init(model_id=model_name, session_id=conv_id, log_dir=TOOL_LOG_DIR)
        if wake_msg:
            steps.append({"ephemeralMessage": wake_msg})

    # 2. UserPromptSubmit Injection (Invocation 0 only)
    if invocation_num == 0 and transcript_path:
        user_prompt = get_latest_user_prompt(transcript_path)
        watch_msg = watch(session_id=conv_id, prompt_text=user_prompt, log_dir=TOOL_LOG_DIR)
        if watch_msg:
            steps.append({"ephemeralMessage": watch_msg})

    print(json.dumps({"injectSteps": steps}))
    return 0

if __name__ == "__main__":
    # Force UTF-8 encoding
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
