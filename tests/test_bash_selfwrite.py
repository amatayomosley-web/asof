"""Tests for execution-window self-write detection (Bash/external edits).

Verifies that a file the substrate edits via an external-volatility tool
(Bash) — detected by mtime aligning with the command's completion — is NOT
misflagged as externally stale, while a genuinely external change (mtime not
aligned with any command completion) still is.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from asof_core.hooks.post_tool import post_tool, SELF_WRITE_MARKER
from asof_core.hooks.watch import watch


def _read_log(log_path: Path) -> list[dict]:
    return [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _log_a_read(log_dir: Path, session_id: str, file_path: str, mtime_epoch: float) -> None:
    """Append a Read record by hand (simulating a prior Read of the file)."""
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool_name": "Read",
        "input_summary": file_path,
        "volatility": "session",
        "mtime_at_read": mtime_epoch,
    }
    (log_dir / f"{session_id}.jsonl").open("a", encoding="utf-8").write(
        json.dumps(rec) + "\n"
    )


def test_bash_selfwrite_not_flagged_stale(tmp_path):
    """A file Read then edited via Bash (mtime ~= command completion) must
    NOT appear as stale — the post_tool concurrent-write detection records a
    self-write that the watch hook honours."""
    log_dir = tmp_path / "tool_log"
    log_dir.mkdir()
    session_id = "test-selfwrite"

    target = tmp_path / "budget.csv"
    target.write_text("total: 4400\n", encoding="utf-8")
    read_mtime = target.stat().st_mtime

    # 1. Substrate Read the file earlier.
    _log_a_read(log_dir, session_id, str(target), read_mtime)

    # 2. A Bash command edits it (sed -i style). Bump its mtime to "now".
    time.sleep(0.05)
    target.write_text("total: 9999\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    os.utime(target, (now.timestamp(), now.timestamp()))

    # 3. PostToolUse fires for the Bash command at completion (~now).
    post_tool(
        session_id=session_id,
        tool_name="Bash",
        tool_input={"command": "sed -i 's/4400/9999/' budget.csv"},
        log_dir=log_dir,
        now=now,
    )

    # A self-write record should now exist for the target.
    records = _read_log(log_dir / f"{session_id}.jsonl")
    sw = [r for r in records if r.get("tool_name") == SELF_WRITE_MARKER]
    assert any(r["input_summary"] == str(target) for r in sw), \
        "expected a _asof_self_write record for the Bash-edited file"

    # 4. Watch should NOT report the file as stale.
    block = watch(session_id=session_id, prompt_text="", log_dir=log_dir, now=now)
    assert str(target) not in block or "File freshness" not in block, \
        f"Bash-edited file was wrongly flagged stale:\n{block}"


def test_external_change_still_flagged_stale(tmp_path):
    """A file changed by something OTHER than a just-completed command
    (mtime not aligned with any command completion) must still be stale."""
    log_dir = tmp_path / "tool_log"
    log_dir.mkdir()
    session_id = "test-external"

    target = tmp_path / "peer_edited.md"
    target.write_text("v1\n", encoding="utf-8")
    read_mtime = target.stat().st_mtime

    _log_a_read(log_dir, session_id, str(target), read_mtime)

    # An external editor changes it; its mtime is well in the PAST relative
    # to the watch/command time (simulate a change not concurrent with any
    # command completion).
    target.write_text("v2 external\n", encoding="utf-8")
    external_mtime = read_mtime + 30.0  # 30s after read
    os.utime(target, (external_mtime, external_mtime))

    # Run a Bash tool whose completion is much LATER (no alignment with the
    # file's mtime → not credited as a self-write).
    much_later = datetime.fromtimestamp(external_mtime + 600, tz=timezone.utc)
    post_tool(
        session_id=session_id,
        tool_name="Bash",
        tool_input={"command": "echo unrelated"},
        log_dir=log_dir,
        now=much_later,
    )

    records = _read_log(log_dir / f"{session_id}.jsonl")
    sw = [r for r in records if r.get("tool_name") == SELF_WRITE_MARKER]
    assert not any(r["input_summary"] == str(target) for r in sw), \
        "external change must NOT be credited as a self-write"

    block = watch(session_id=session_id, prompt_text="", log_dir=log_dir, now=much_later)
    assert "File freshness" in block and str(target) in block, \
        f"genuinely-external change should still be flagged stale:\n{block}"
