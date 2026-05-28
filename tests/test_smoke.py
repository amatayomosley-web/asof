"""Smoke tests for AsOf core modules.

These verify the load-bearing computations behave correctly on the
canonical test cases. Run with: pytest tests/

For unit tests by module, see tests/test_<module>.py.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


def test_version_compatibility():
    from asof_core.version import SCHEMA_VERSION, MIN_PROSE_VERSION, is_compatible

    assert is_compatible(SCHEMA_VERSION, MIN_PROSE_VERSION)
    assert is_compatible("0.2.5", "0.1.0")
    assert not is_compatible("1.0.0", "0.1.0")
    assert not is_compatible("0.0.9", "0.1.0")


def test_cutoff_lookup():
    from asof_core.cutoffs import lookup_cutoff, gap_to_now

    assert lookup_cutoff("claude-opus-4-7") == "2026-01"
    assert lookup_cutoff("gemini-3.5-flash") == "2026-01"
    assert lookup_cutoff("nonexistent-model") is None

    gap = gap_to_now("2026-01", now=date(2026, 5, 28))
    assert gap["days"] == 147
    assert gap["months"] == 4
    assert "months ago" in gap["human"]


def test_nvda_timestamp_parsing():
    """The NVDA hero example — Q3 2025 should resolve to ~Nov 2025."""
    from asof_core.timestamps import find_timestamps, parse_quarter, quarters_since_announcement

    base = date(2026, 5, 28)
    text = "NVDA Q3 2025 earnings announced November 2025. What about next quarter?"
    matches = find_timestamps(text, base_date=base)

    # Should catch Q3 2025 and November 2025
    matched_raws = [m["raw"] for m in matches]
    assert "Q3 2025" in matched_raws
    assert "November 2025" in matched_raws

    # Quarter parser
    q = parse_quarter("Q3 2025")
    assert q["year"] == 2025
    assert q["quarter"] == 3
    assert q["announce_date"] == date(2025, 11, 15)

    # Two quarters should have passed since announcement (Nov 2025 → May 2026)
    quarters = quarters_since_announcement(q, base_date=base)
    assert quarters >= 2


def test_naomi_arithmetic_boundary():
    """Naomi's RT concern: 147 days ago should resolve precisely.

    LLM chat arithmetic fails at this boundary; Python doesn't.
    """
    from asof_core.timestamps import find_timestamps

    base = date(2026, 5, 28)
    matches = find_timestamps("from 147 days ago", base_date=base)
    assert len(matches) == 1
    assert matches[0]["resolved"] == date(2026, 1, 1)  # 2026-05-28 - 147 days


def test_pattern_matcher_default():
    from asof_core.patterns import PatternMatcher

    m = PatternMatcher()

    # High-confidence cases should match
    assert m.has_match("what is the current price of AAPL")
    assert m.has_match("is the API still working")

    # Cases that should not match (no temporal/dynamic content)
    assert not m.has_match("refactor this function")
    assert not m.has_match("explain how lists work in Python")


def test_pattern_matcher_with_finance():
    from asof_core.patterns import PatternMatcher

    m = PatternMatcher(domains=["finance"])
    # Finance-pack pattern should fire on this:
    matches = m.match_all("Should I buy AAPL at this price?")
    assert any(x["tier"].startswith("domain:") for x in matches)


def test_stat_helpers():
    from asof_core.stat import stat_now, classify_file_freshness, format_duration

    # format_duration
    assert format_duration(45) == "45s"
    assert format_duration(125) == "2m"
    assert format_duration(3700) == "1h1m"
    assert format_duration(90000) == "1d1h"

    # Stat a known file (this test file itself)
    s = stat_now(__file__)
    assert s["exists"]
    assert s["mtime_epoch"] is not None
    assert s["size_bytes"] > 0


def test_file_freshness_classification():
    """File freshness: unchanged mtime → fresh; moved mtime without
    matching self-write → stale."""
    from asof_core.stat import classify_file_freshness

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("original")
        temp_path = f.name

    import os
    original_mtime = os.path.getmtime(temp_path)

    # Fresh — mtime unchanged
    v = classify_file_freshness(temp_path, original_mtime)
    assert v["verdict"] == "fresh"

    # Simulate an external write — bump mtime forward
    new_mtime = original_mtime + 100
    os.utime(temp_path, (new_mtime, new_mtime))
    v = classify_file_freshness(temp_path, original_mtime)
    assert v["verdict"] == "stale"

    # Self-write exclusion: pass new_mtime as a later_self_write
    v = classify_file_freshness(temp_path, original_mtime, later_self_writes=[new_mtime])
    assert v["verdict"] == "fresh"

    os.unlink(temp_path)


def test_end_to_end_session():
    """End-to-end: session_init → post_tool → watch produce the expected
    behavior shape on the NVDA-style scenario."""
    from asof_core.hooks import session_init, post_tool, watch

    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "log"
        session_id = "smoke-test"

        # Session init should include cutoff info
        init = session_init(model_id="claude-opus-4-7", session_id=session_id, log_dir=log_dir)
        assert "Training cutoff" in init
        assert "2026-01" in init

        # Empty prompt should produce empty watch output
        out = watch(session_id=session_id, prompt_text="", log_dir=log_dir)
        assert out == ""

        # NVDA prompt should produce non-empty output with parsed timestamps
        nvda = "NVDA Q3 2025 earnings announced November 2025"
        out = watch(session_id=session_id, prompt_text=nvda, log_dir=log_dir)
        assert "Q3 2025" in out
        assert "Timestamps in your message" in out


if __name__ == "__main__":
    # Allow running directly without pytest for quick smoke
    import sys
    test_version_compatibility()
    test_cutoff_lookup()
    test_nvda_timestamp_parsing()
    test_naomi_arithmetic_boundary()
    test_pattern_matcher_default()
    test_pattern_matcher_with_finance()
    test_stat_helpers()
    test_file_freshness_classification()
    test_end_to_end_session()
    print("All smoke tests passed.")
