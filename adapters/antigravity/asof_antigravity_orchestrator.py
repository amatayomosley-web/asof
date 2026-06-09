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

# Add the parent directory (asof project root) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from asof_core.hooks import session_init, post_tool, watch, surface_staleness

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

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _strip_user_request(content: str) -> str:
    """Antigravity wraps the prompt as <USER_REQUEST>..</USER_REQUEST> followed by
    an <ADDITIONAL_METADATA> block. Scan only the user's own words so injected
    metadata (e.g. 'The current local time is ...') cannot trip the matchers."""
    if not content:
        return ""
    m = USER_REQUEST_RE.search(content)
    return m.group(1).strip() if m else content.strip()


def get_user_prompt_state(conv_id: str) -> int:
    """Highest USER_INPUT step_index already handed to watch(). -1 if none."""
    state_file = STATE_DIR / f"state_{conv_id}.user.json"
    if not state_file.exists():
        return -1
    try:
        return json.loads(state_file.read_text(encoding="utf-8")).get("last_watched_user_step", -1)
    except Exception:
        return -1


def save_user_prompt_state(conv_id: str, step_index: int):
    state_file = STATE_DIR / f"state_{conv_id}.user.json"
    tmp_file = STATE_DIR / f"state_{conv_id}.user.json.tmp"
    try:
        tmp_file.write_text(json.dumps({"last_watched_user_step": step_index}), encoding="utf-8")
        os.replace(tmp_file, state_file)
    except Exception:
        pass


def _scan_newest_user_input(transcript_path: str) -> tuple[int, str]:
    """(step_index, raw_content) of the highest-step USER_INPUT, or (-1, '')."""
    best_idx = -1
    best_content = ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("source") == "USER_EXPLICIT" and entry.get("type") == "USER_INPUT":
                    si = entry.get("step_index", -1)
                    if si >= best_idx:
                        best_idx = si
                        best_content = entry.get("content", "")
    except Exception:
        pass
    return best_idx, best_content


def get_new_user_prompt(transcript_path: str, last_step: int, poll: bool = False) -> tuple[int, str]:
    """Newest USER_INPUT with step_index > last_step, wrapper stripped.

    Antigravity fires PreInvocation many times per turn, and the current turn's
    USER_INPUT is not reliably flushed to transcript.jsonl by invocation 0. The
    step-index cursor (last_step) makes the scan fire exactly once per prompt, at
    the first invocation where it is on disk. The optional bounded poll is a
    best-effort same-turn catch for zero-tool single-shot turns; when it misses,
    the next invocation's cursor comparison catches the prompt with no loss.

    Returns (new_step, prompt_text), or (last_step, '') when nothing is new.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return last_step, ""
    import time
    attempts = 10 if poll else 1  # ~1s ceiling at 100ms, well under the 5s hook timeout
    for i in range(attempts):
        idx, content = _scan_newest_user_input(transcript_path)
        if idx > last_step and content:
            return idx, _strip_user_request(content)
        if poll and i < attempts - 1:
            time.sleep(0.1)
    return last_step, ""

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

    # 1b. Tool-boundary staleness (Tier 2). After logging this invocation's new
    #     tool events (above), surface any Read file that went stale from a
    #     background/external edit so the model sees it before its next step.
    #     Shared surfacing state with the per-prompt watch below → surfaces once.
    if invocation_num > 0 and transcript_path:
        stale_block = surface_staleness(conv_id, log_dir=TOOL_LOG_DIR)
        if stale_block:
            steps.append({"ephemeralMessage": stale_block})

    # 2. UserPromptSubmit injection — stateful. Fires watch() once per user
    #    prompt, at the first invocation where that prompt is on disk (NOT gated
    #    to invocation 0, which races the transcript flush). watch() expects one
    #    call per turn; the step-index cursor guarantees exactly that.
    if transcript_path:
        last_user_step = get_user_prompt_state(conv_id)
        new_step, prompt_text = get_new_user_prompt(
            transcript_path, last_user_step, poll=(invocation_num == 0)
        )
        if new_step > last_user_step and prompt_text:
            watch_msg = watch(session_id=conv_id, prompt_text=prompt_text, log_dir=TOOL_LOG_DIR)
            if watch_msg:
                steps.append({"ephemeralMessage": watch_msg})
            save_user_prompt_state(conv_id, new_step)

    print(json.dumps({"injectSteps": steps}))
    return 0

if __name__ == "__main__":
    # Force UTF-8 encoding
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    sys.exit(main())
