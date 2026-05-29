"""Tests for the AsOf MCP server adapter — the three tools register and return
the expected shapes."""
from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1] / "adapters" / "mcp" / "server.py"
_spec = importlib.util.spec_from_file_location("asof_mcp_server", _SERVER)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def test_asof_now_returns_todays_utc_date():
    out = server.asof_now()
    assert {"iso", "date", "weekday", "unix"} <= set(out)
    assert out["date"] == datetime.now(timezone.utc).date().isoformat()


def test_asof_query_timestamp_is_stale():
    out = server.asof_query("2020-01-01", kind_hint="timestamp")
    assert out["kind"] == "timestamp"
    assert out["verdict"] == "stale"
    assert out["detail"]["gap_days"] > 0


def test_asof_query_text_without_temporal_is_unknown():
    out = server.asof_query("just some prose with no dates", kind_hint="text")
    assert out["kind"] == "text"
    assert out["verdict"] == "unknown"


def test_asof_cutoff_unknown_model_returns_none():
    out = server.asof_cutoff("totally-made-up-model-zzz")
    assert out["model"] == "totally-made-up-model-zzz"
    assert out["cutoff"] is None


def test_asof_cutoff_known_model_has_gap():
    # claude-opus-4-8 was registered in asof_core/cutoffs.py this cycle.
    out = server.asof_cutoff("claude-opus-4-8")
    assert out["cutoff"], "expected a registered cutoff for claude-opus-4-8"
    assert "months" in out


def test_three_tools_are_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"asof_now", "asof_query", "asof_cutoff"} <= names
