"""Tests for quote-the-datum: capture a read-content excerpt and co-locate it
with the STALE warning (surfacing co-location approximation)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from asof_core.hooks.post_tool import post_tool
from asof_core.hooks.watch import watch
from asof_core.output import _format_file_freshness


def _read_log(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def test_excerpt_captured_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ASOF_QUOTE_DATUM", "1")
    f = tmp_path / "src.py"
    f.write_text("print('hello world')\n", encoding="utf-8")
    logdir = tmp_path / "log"
    post_tool(
        session_id="s1", tool_name="Read",
        tool_input={"file_path": str(f)},
        tool_response={"content": "print('hello world')\n\n# trailing comment"},
        log_dir=logdir,
    )
    reads = [r for r in _read_log(logdir / "s1.jsonl") if r.get("tool_name") == "Read"]
    assert reads, "Read record should be logged"
    assert "print('hello world')" in (reads[-1].get("read_excerpt") or "")


def test_excerpt_absent_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ASOF_QUOTE_DATUM", raising=False)
    # Isolate HOME so a real ~/.asof/config.json can't enable it.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "src.py"
    f.write_text("x = 1\n", encoding="utf-8")
    logdir = tmp_path / "log"
    post_tool(
        session_id="s1", tool_name="Read",
        tool_input={"file_path": str(f)},
        tool_response={"content": "x = 1\n"},
        log_dir=logdir,
    )
    reads = [r for r in _read_log(logdir / "s1.jsonl") if r.get("tool_name") == "Read"]
    assert reads and "read_excerpt" not in reads[-1]


def test_excerpt_truncated_and_whitespace_collapsed(tmp_path, monkeypatch):
    monkeypatch.setenv("ASOF_QUOTE_DATUM", "1")
    f = tmp_path / "big.txt"
    f.write_text("data", encoding="utf-8")
    logdir = tmp_path / "log"
    long_content = "word " * 200  # 1000 chars with spaces/newlines collapsed
    post_tool(
        session_id="s1", tool_name="Read",
        tool_input={"file_path": str(f)},
        tool_response={"content": long_content},
        log_dir=logdir,
    )
    rec = [r for r in _read_log(logdir / "s1.jsonl") if r.get("tool_name") == "Read"][-1]
    ex = rec["read_excerpt"]
    assert len(ex) <= 142  # 140 + ellipsis
    assert ex.endswith("…")
    assert "\n" not in ex


def test_format_renders_excerpt_line():
    lines = _format_file_freshness([
        {"path": "/a.py", "reason": "size changed 5->9 bytes after read",
         "read_excerpt": "print('hello world')"},
    ])
    blob = "\n".join(lines)
    assert "you read:" in blob
    assert "print('hello world')" in blob


def test_format_no_excerpt_no_extra_line():
    lines = _format_file_freshness([{"path": "/a.py", "reason": "size changed"}])
    assert not any("you read" in line for line in lines)


def test_watch_end_to_end_quotes_datum(tmp_path, monkeypatch):
    # Isolate HOME so surfacing state starts fresh (first-surface fires).
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "watched.py"
    f.write_text("original short", encoding="utf-8")
    past = time.time() - 200
    logdir = tmp_path / "log"
    logdir.mkdir()
    rec = {
        "ts": "2026-05-28T00:00:00Z", "tool_name": "Read", "input_summary": str(f),
        "mtime_at_read": past, "size_bytes": len("original short"),
        "read_excerpt": "original short content snippet",
    }
    (logdir / "sess.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    # Make it stale: grow the file (size differs => confident stale).
    f.write_text("original short content is now considerably longer than before", encoding="utf-8")
    now = time.time()
    os.utime(f, (now, now))

    out = watch(session_id="sess", prompt_text="", log_dir=logdir,
                config={}, now=datetime.now(timezone.utc))
    assert "STALE" in out
    assert "you read:" in out
    assert "original short content snippet" in out
