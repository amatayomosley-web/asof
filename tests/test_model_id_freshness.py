"""Tests for the Claude Code adapter's model-ID detection
(_find_model_from_transcript).

Regression target: a 4-7 -> 4-8 substrate upgrade made AsOf report the stale
4-7 cutoff because detection took the FIRST assistant message's model from the
latest-by-mtime transcript. The fix targets this session's transcript by id and
takes the LAST model. Uses a fake ~/.claude/projects tree; no real Claude Code.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

# Load the adapter module by path (no import-time side effects — the stdout
# rebind now lives in main(), not at module top).
_ADAPTER = (Path(__file__).resolve().parent.parent
            / "adapters" / "claude_code" / "session_init.py")
_spec = importlib.util.spec_from_file_location("asof_cc_session_init", _ADAPTER)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _assistant(model: str) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "model": model}})


def _user(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def _write_transcript(path: Path, lines: list[str], *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture(autouse=True)
def _fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    proj = tmp_path / ".claude" / "projects"
    proj.mkdir(parents=True, exist_ok=True)
    return proj


def test_returns_last_model_not_first(_fake_home):
    # Resumed/upgraded session: early messages 4-7, latest 4-8.
    sid = "sess-upgrade"
    _write_transcript(_fake_home / "projA" / f"{sid}.jsonl", [
        _assistant("claude-opus-4-7"),
        _user("more"),
        _assistant("claude-opus-4-7"),
        _user("even more"),
        _assistant("claude-opus-4-8"),   # current model
    ])
    assert _mod._find_model_from_transcript(sid) == "claude-opus-4-8"


def test_targets_session_transcript_over_more_recent_other(_fake_home):
    # This session's transcript is OLDER by mtime than another session's file.
    # Old code picked latest-by-mtime (wrong session); the fix picks by id.
    sid = "sess-mine"
    _write_transcript(_fake_home / "projA" / f"{sid}.jsonl",
                      [_assistant("claude-opus-4-8")], mtime=time.time() - 500)
    _write_transcript(_fake_home / "projB" / "other-session.jsonl",
                      [_assistant("gemini-3.5-flash")], mtime=time.time())
    assert _mod._find_model_from_transcript(sid) == "claude-opus-4-8"


def test_falls_back_to_latest_by_mtime_when_id_unknown(_fake_home):
    _write_transcript(_fake_home / "projA" / "a.jsonl",
                      [_assistant("claude-opus-4-7")], mtime=time.time() - 500)
    _write_transcript(_fake_home / "projB" / "b.jsonl",
                      [_assistant("claude-haiku-4-5")], mtime=time.time())
    # No matching session id -> newest transcript's last model.
    assert _mod._find_model_from_transcript("no-such-session") == "claude-haiku-4-5"


def test_none_when_no_assistant_model(_fake_home):
    sid = "sess-empty"
    _write_transcript(_fake_home / "projA" / f"{sid}.jsonl",
                      [_user("hi"), _user("still no model")])
    assert _mod._find_model_from_transcript(sid) is None


def test_none_when_no_projects(_fake_home):
    assert _mod._find_model_from_transcript("anything") is None


def test_skips_malformed_lines(_fake_home):
    sid = "sess-malformed"
    _write_transcript(_fake_home / "projA" / f"{sid}.jsonl", [
        "{ not json",
        _assistant("claude-opus-4-8"),
        "}}}garbage",
    ])
    assert _mod._find_model_from_transcript(sid) == "claude-opus-4-8"
