"""Unit tests for the AsOf Antigravity Adapter Orchestrator.

Tests the PreInvocation event synthesis by mocking stdin/stdout,
patching filesystem paths, and feeding mock transcripts.
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Add adapters to path to ensure importability during tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.antigravity import asof_antigravity_orchestrator as orch


@pytest.fixture
def mock_asof_dirs():
    """Create a temporary directory structure to isolate ASOF_DIR state."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        state_dir = tmp_path / "state"
        tool_log_dir = tmp_path / "tool_log"
        state_dir.mkdir(parents=True)
        tool_log_dir.mkdir(parents=True)

        with patch.object(orch, "ASOF_DIR", tmp_path), \
             patch.object(orch, "STATE_DIR", state_dir), \
             patch.object(orch, "TOOL_LOG_DIR", tool_log_dir):
            yield tmp_path, state_dir, tool_log_dir


def test_session_start_wake(mock_asof_dirs):
    """Test that a new conversationId triggers a SessionStart wake message."""
    tmp_path, _, _ = mock_asof_dirs

    mock_input = {
        "conversationId": "test-conv-123",
        "invocationNum": 0,
        "transcriptPath": None
    }

    with patch("sys.stdin", sys.stdin):
        with patch("sys.stdin.read", return_value=json.dumps(mock_input)):
            with patch("sys.stdout.write") as mock_write:
                # Run main and capture stdout printed JSON
                exit_code = orch.main()
                assert exit_code == 0
                
                # Retrieve the printed payload
                calls = mock_write.call_args_list
                printed_text = "".join(call[0][0] for call in calls)
                output_data = json.loads(printed_text)
                
                # Verify SessionStart message was injected
                assert "injectSteps" in output_data
                assert len(output_data["injectSteps"]) == 1
                wake_msg = output_data["injectSteps"][0]["ephemeralMessage"]
                assert "=== AsOf v1.0.0 ===" in wake_msg
                assert "Training cutoff" in wake_msg
                assert "Directive:" in wake_msg


def test_post_tool_use_transcript_logging(mock_asof_dirs):
    """Test that invocationNum > 0 parses transcript.jsonl and logs tool calls."""
    _, _, tool_log_dir = mock_asof_dirs

    # Create a mock transcript file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl", encoding="utf-8") as tf:
        transcript_path = tf.name
        
        # Step 1: User prompt
        tf.write(json.dumps({
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "read the project settings file please"
        }) + "\n")
        
        # Step 2: Model calls view_file
        tf.write(json.dumps({
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "tool_calls": [
                {
                    "name": "view_file",
                    "args": {
                        "AbsolutePath": "C:/project/settings.json"
                    }
                }
            ],
            "created_at": "2026-05-27T18:00:00Z"
        }) + "\n")

    try:
        conv_id = "test-conv-logging"
        mock_input = {
            "conversationId": conv_id,
            "invocationNum": 1,
            "transcriptPath": transcript_path
        }

        # Initialize session state so it's not treated as a new session wake
        state_file = tmp_path_state = mock_asof_dirs[0] / "session_state.json"
        state_file.write_text(json.dumps({"last_conversation_id": conv_id}), encoding="utf-8")

        with patch("sys.stdin.read", return_value=json.dumps(mock_input)):
            with patch("sys.stdout.write") as mock_write:
                exit_code = orch.main()
                assert exit_code == 0

                # Tool logging shouldn't inject user-facing prompts on invocation > 0
                printed_text = "".join(call[0][0] for call in mock_write.call_args_list)
                output_data = json.loads(printed_text)
                assert len(output_data["injectSteps"]) == 0

                # Assert tool call log file was created
                log_file = tool_log_dir / f"{conv_id}.jsonl"
                assert log_file.exists()
                
                log_content = log_file.read_text(encoding="utf-8")
                logged_events = [json.loads(line) for line in log_content.splitlines() if line]
                assert len(logged_events) == 1
                assert logged_events[0]["tool_name"] == "view_file"
                assert logged_events[0]["target"] == "C:/project/settings.json"
                assert logged_events[0]["volatility"] == "session"
    finally:
        os.unlink(transcript_path)


def test_user_prompt_submit_freshness(mock_asof_dirs):
    """Test that modified files are correctly detected and raise warnings on turn start."""
    tmp_path, _, tool_log_dir = mock_asof_dirs
    conv_id = "test-conv-freshness"

    # Set up active session state so it's not a wake turn
    state_file = tmp_path / "session_state.json"
    state_file.write_text(json.dumps({"last_conversation_id": conv_id}), encoding="utf-8")

    # Create a temp file to simulate a target file being read
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as target_tf:
        target_path = target_tf.name
        target_tf.write("original settings content")

    try:
        # Pre-populate the tool log to indicate the file was read in the past (e.g. 5 minutes ago)
        read_time = "2026-05-27T18:00:00Z"
        log_file = tool_log_dir / f"{conv_id}.jsonl"
        log_file.write_text(json.dumps({
            "ts": read_time,
            "step_index": 1,
            "tool_name": "view_file",
            "target": target_path,
            "volatility": "session"
        }) + "\n", encoding="utf-8")

        # Simulate an external write to target_path AFTER read_time
        # Bump the file modified time forward
        os.utime(target_path, (datetime.now().timestamp() + 10, datetime.now().timestamp() + 10))

        # Create a mock transcript containing the user prompt
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl", encoding="utf-8") as tf:
            transcript_path = tf.name
            tf.write(json.dumps({
                "step_index": 2,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "update the settings file"
            }) + "\n")

        try:
            mock_input = {
                "conversationId": conv_id,
                "invocationNum": 0,
                "transcriptPath": transcript_path
            }

            with patch("sys.stdin.read", return_value=json.dumps(mock_input)):
                with patch("sys.stdout.write") as mock_write:
                    exit_code = orch.main()
                    assert exit_code == 0

                    printed_text = "".join(call[0][0] for call in mock_write.call_args_list)
                    output_data = json.loads(printed_text)
                    
                    # Verify stale file warning was injected
                    assert len(output_data["injectSteps"]) == 1
                    ephemeral_msg = output_data["injectSteps"][0]["ephemeralMessage"]
                    assert "=== AsOf Freshness Watch ===" in ephemeral_msg
                    assert "STALE" in ephemeral_msg
                    # Path check (normalize slash)
                    assert target_path.replace("\\", "/") in ephemeral_msg.replace("\\", "/")
                    assert "WARNING: 1 files in working set are stale" in ephemeral_msg
        finally:
            os.unlink(transcript_path)
    finally:
        os.unlink(target_path)


def test_user_prompt_submit_temporal_cues(mock_asof_dirs):
    """Test that time-sensitive phrasing in user prompt triggers alerts."""
    tmp_path, _, _ = mock_asof_dirs
    conv_id = "test-conv-temporal"

    # Set up active session state so it's not a wake turn
    state_file = tmp_path / "session_state.json"
    state_file.write_text(json.dumps({"last_conversation_id": conv_id}), encoding="utf-8")

    # Create mock transcript with user prompt containing temporal cues
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl", encoding="utf-8") as tf:
        transcript_path = tf.name
        tf.write(json.dumps({
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "what is the current status of our deployment and today's news?"
        }) + "\n")

    try:
        mock_input = {
            "conversationId": conv_id,
            "invocationNum": 0,
            "transcriptPath": transcript_path
        }

        with patch("sys.stdin.read", return_value=json.dumps(mock_input)):
            with patch("sys.stdout.write") as mock_write:
                exit_code = orch.main()
                assert exit_code == 0

                printed_text = "".join(call[0][0] for call in mock_write.call_args_list)
                output_data = json.loads(printed_text)

                assert len(output_data["injectSteps"]) == 1
                ephemeral_msg = output_data["injectSteps"][0]["ephemeralMessage"]
                assert "=== AsOf Freshness Watch ===" in ephemeral_msg
                assert "Time-sensitive phrasing detected" in ephemeral_msg
                assert "current status" in ephemeral_msg or "today's news" in ephemeral_msg
    finally:
        os.unlink(transcript_path)
