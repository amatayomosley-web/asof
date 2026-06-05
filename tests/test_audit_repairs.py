"""Regression tests for the 2026-06-04 external-validation audit fixes.

Each test pins a verified bug from staging/asof_fixlist.md so the fix can't
silently regress. Numbered to the fix-list.
"""
from __future__ import annotations

import os
import time

import pytest


# --- #7 [BREAKING] no per-path read dedup -> false STALE after a fresh re-read
def test_evaluate_working_set_dedupes_to_latest_read(tmp_path):
    from asof_core.hooks.watch import _evaluate_working_set

    f = tmp_path / "doc.md"
    f.write_text("content", encoding="utf-8")
    cur = os.path.getmtime(str(f))

    older = {"tool_name": "Read", "input_summary": str(f),
             "mtime_at_read": cur - 5000.0, "size_bytes": 7}
    fresh = {"tool_name": "Read", "input_summary": str(f),
             "mtime_at_read": cur, "size_bytes": 7}

    # Control: the stale earlier read ALONE is STALE.
    assert _evaluate_working_set([older]), "earlier read alone should be stale (control)"
    # Fix: a later fresh re-read of the same path suppresses the earlier stale one.
    assert _evaluate_working_set([older, fresh]) == [], "fresh re-read must dedupe out the stale earlier read"


# --- #6 [CORE] prefix-match returned a confidently-wrong cutoff for partial IDs
def test_registry_match_refuses_partial_ids():
    from asof_core import cutoffs

    assert cutoffs._registry_match("claude-opus-4-8") == "2026-01"           # exact
    assert cutoffs._registry_match("claude-opus-4-7-20260115") == "2026-01"  # longest forward-prefix
    assert cutoffs._registry_match("deepseek-r1:32b") == "2024-09"           # ollama tag form
    # A bare/partial ID must NOT resolve to a longer specific row's cutoff.
    assert cutoffs._registry_match("claude-opus-4") is None
    assert cutoffs._registry_match("gpt") is None


# --- #9 [correctness] _detect_kind misrouted deepseek/gemma model IDs as text
def test_detect_kind_routes_registered_models():
    from asof_core.query import _detect_kind

    assert _detect_kind("deepseek-r1") == "model"
    assert _detect_kind("gemma4-e4b") == "model"
    assert _detect_kind("claude-opus-4-8") == "model"   # known prefix path
    assert _detect_kind("just some prose") == "text"


# --- #27 [hygiene] approximate cutoffs resolved as authoritative
def test_resolve_cutoff_flags_approximate_rows():
    from asof_core.cutoffs import resolve_cutoff

    assert resolve_cutoff("gemma4-e4b", allow_ollama_scan=False)["source"] == "registry-approximate"
    assert resolve_cutoff("claude-opus-4-8", allow_ollama_scan=False)["source"] == "registry"


# --- #15 [coverage] ISO datetimes with a T time were silently undetected
def test_iso_datetime_with_T_is_detected():
    from asof_core.timestamps import find_timestamps

    res = find_timestamps("event logged at 2026-05-24T16:00:00Z in the report")
    assert "2026-05-24" in [r["raw"] for r in res]


# --- #16 [finance] fiscal-year quarters conflated with calendar years
def test_parse_quarter_flags_fiscal():
    from asof_core.timestamps import parse_quarter

    assert parse_quarter("Q3 FY2025")["fiscal"] is True
    assert parse_quarter("Q3 2025")["fiscal"] is False


# --- #8 [correctness] mtime going backwards read as "fresh"
def test_mtime_backwards_with_changed_content_is_stale(tmp_path):
    from asof_core.stat import classify_file_freshness

    f = tmp_path / "c.txt"
    f.write_text("original", encoding="utf-8")            # 8 bytes
    # Simulate a read whose captured mtime is AHEAD of the file's current mtime
    # (a restore / checkout to an older mtime moves it backwards).
    read_mtime = os.path.getmtime(str(f)) + 5000.0
    f.write_text("changed!!", encoding="utf-8")           # 9 bytes, mtime ~ now
    now = time.time()
    os.utime(f, (now, now))

    v = classify_file_freshness(str(f), read_mtime, size_at_read=len("original"))
    assert v["verdict"] == "stale", v


# --- #20 [latent] hash rung was unreachable without size_at_read
def test_hash_rung_runs_without_size(tmp_path):
    from asof_core.stat import classify_file_freshness, content_hash

    f = tmp_path / "d.txt"
    f.write_text("same bytes", encoding="utf-8")
    read_mtime = os.path.getmtime(str(f)) - 5000.0   # mtime moved forward since read
    hash_at_read = content_hash(str(f))
    now = time.time()
    os.utime(f, (now, now))                           # bump mtime, identical content

    # No size_at_read provided — pre-fix this skipped the hash rung and returned
    # stale; now the hash rung runs and recognizes the no-op write.
    v = classify_file_freshness(str(f), read_mtime, hash_at_read=hash_at_read)
    assert v["verdict"] == "fresh", v


# --- #14 [BUILD] ASOF_MODE / ASOF_DOMAINS documented but never read from env
def test_asof_mode_env_silences(tmp_path, monkeypatch):
    from asof_core.hooks.watch import watch

    prompt = "what changed on 2020-01-01?"   # dated -> normally emits a block
    monkeypatch.delenv("ASOF_MODE", raising=False)
    assert watch(session_id="rep-normal", prompt_text=prompt, log_dir=tmp_path) != ""

    monkeypatch.setenv("ASOF_MODE", "silent")
    assert watch(session_id="rep-silent", prompt_text=prompt, log_dir=tmp_path) == ""


def test_asof_domains_env_overlay_runs(tmp_path, monkeypatch):
    from asof_core.hooks.watch import watch

    monkeypatch.setenv("ASOF_DOMAINS", "finance,travel")
    # The env-domains overlay path must run cleanly (packs loaded into the matcher).
    watch(session_id="rep-domains", prompt_text="hello there", log_dir=tmp_path)


# --- #26 [hygiene] dead UNKNOWN-PENDING sentinel; unknown-model path stays clean
def test_query_model_unknown_is_clean():
    from asof_core.query import query

    out = query("totally-unregistered-model-xyz", kind_hint="model")
    assert out["kind"] == "model"
    assert out["verdict"] == "unknown"


# --- #4 [BUILD] is_compatible() wired into session-init as a version-skew guard
def test_incompatibility_notice():
    from asof_core.version import incompatibility_notice, SCHEMA_VERSION

    assert incompatibility_notice(SCHEMA_VERSION, SCHEMA_VERSION) is None
    note = incompatibility_notice("0.1.0", "0.2.0")          # prose floor ahead of hook
    assert note is not None and "INCOMPATIBLE" in note
    assert incompatibility_notice("1.0.0", "0.1.0") is not None  # major mismatch
    assert incompatibility_notice("garbage", "0.1.0") is None    # malformed -> no false alarm


def test_session_init_emits_incompatible(monkeypatch, tmp_path):
    from asof_core.hooks import session_init

    monkeypatch.delenv("ASOF_PROSE_VERSION", raising=False)
    out_ok = session_init(model_id="claude-opus-4-8", session_id="vg-ok",
                          log_dir=tmp_path, prose_min_version="0.1.0")
    assert "INCOMPATIBLE" not in out_ok
    out_bad = session_init(model_id="claude-opus-4-8", session_id="vg-bad",
                           log_dir=tmp_path, prose_min_version="9.9.9")
    assert "INCOMPATIBLE" in out_bad


def test_installer_reads_skill_min_version():
    from adapters.claude_code.install import _read_skill_min_version

    assert _read_skill_min_version() == "0.1.0"
