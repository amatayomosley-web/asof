"""The Claude Code watch adapter prints nothing on a machine turn, and stays the same on a person's turn.

Runs the adapter as Claude Code does, a child process fed the event on stdin, because the module rewraps
stdout on import.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUN = "import sys; sys.path.insert(0, sys.argv[1]); from adapters.claude_code.watch import main; sys.exit(main())"
FILE = (REPO / "README.md").as_posix()


def fire(prompt: str, session: str) -> str:
    event = json.dumps({"session_id": session, "prompt": prompt})
    done = subprocess.run([sys.executable, "-c", RUN, str(REPO)], input=event, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.parametrize("opening", ["<task-notification>", "=== Transcript of", "<system-reminder>", "Caveat:"])
def test_machine_turn_prints_nothing(opening: str) -> None:
    assert fire(f"{opening}\n<output-file>{FILE}</output-file>", "test-machine-turn") == ""


def test_leading_whitespace_is_still_a_machine_turn() -> None:
    assert fire(f"\n  <task-notification>\n<output-file>{FILE}</output-file>", "test-machine-space") == ""


def test_a_person_naming_the_same_file_still_gets_the_block() -> None:
    assert "AsOf" in fire(f"please look at {FILE}", "test-person-turn")
