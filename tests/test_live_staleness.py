"""Unit tests for the live file-staleness harness — verify AsOf's real
detection fires in the loop and the scoring classifies behaviour correctly,
using mock models (no Ollama/DeepSeek needed)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_H = Path(__file__).resolve().parent / "abtests" / "live_staleness.py"
_spec = importlib.util.spec_from_file_location("asof_live_staleness", _H)
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)


def _ignore_mock(messages):
    # Commits the stale token, never re-reads — the failure AsOf should prevent.
    return f"deploy --token {live.OLD_TOKEN}"


def _heed_mock(messages):
    text = " ".join(m["content"] for m in messages)
    if "Current contents of" in text:        # was given fresh content after re-read
        return f"deploy --token {live.NEW_TOKEN}"
    if "STALE" in text:                       # saw AsOf's verdict -> re-read
        return f"READ_FILE: {live.CONFIG_NAME}"
    return f"deploy --token {live.OLD_TOKEN}"  # no signal -> uses stale


def test_condition_b_really_detects_staleness(tmp_path):
    out = live.run_cell(_ignore_mock, "B", workspace=tmp_path / "b", session_id="t-b")
    # AsOf's own detection ran over the recorded read + changed file:
    assert out["asof_fired"] is True
    # a model that ignores the verdict commits the stale token:
    assert out["verdict"] == "stale"


def test_condition_a_injects_nothing(tmp_path):
    out = live.run_cell(_ignore_mock, "A", workspace=tmp_path / "a", session_id="t-a")
    assert out["asof_fired"] is False
    assert out["verdict"] == "stale"


def test_heeding_model_only_goes_fresh_under_asof(tmp_path):
    b = live.run_cell(_heed_mock, "B", workspace=tmp_path / "hb", session_id="t-hb")
    a = live.run_cell(_heed_mock, "A", workspace=tmp_path / "ha", session_id="t-ha")
    # With AsOf's STALE block, the model re-reads and lands on the fresh token:
    assert b["reread"] is True and b["verdict"] == "fresh"
    # Without it, the same model commits the stale token:
    assert a["reread"] is False and a["verdict"] == "stale"
