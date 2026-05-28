"""Tests for Class C precision: size rung + content-hash confirmation."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from asof_core.stat import classify_file_freshness, content_hash


def _age_read_mtime(path, seconds_ago=100.0):
    """Set a file's mtime into the past and return that mtime (simulating a
    prior read), so 'now' is clearly after it."""
    past = time.time() - seconds_ago
    os.utime(path, (past, past))
    return past


def test_size_differs_is_confident_stale(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    read_mtime = _age_read_mtime(f)
    size_at_read = 5
    # grow the file (mtime moves to ~now, size changes)
    f.write_text("hello world!!", encoding="utf-8")
    v = classify_file_freshness(str(f), read_mtime, size_at_read=size_at_read)
    assert v["verdict"] == "stale"
    assert "size changed" in v["reason"]


def test_noop_write_identical_content_is_fresh(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("same content", encoding="utf-8")
    read_mtime = _age_read_mtime(f)
    size_at_read = len("same content")
    hash_at_read = content_hash(str(f))
    # "no-op write": rewrite identical bytes, bump mtime to now
    f.write_text("same content", encoding="utf-8")
    now = time.time()
    os.utime(f, (now, now))
    v = classify_file_freshness(
        str(f), read_mtime, size_at_read=size_at_read, hash_at_read=hash_at_read
    )
    assert v["verdict"] == "fresh", v
    assert "no-op" in v["reason"]


def test_same_size_changed_content_is_stale(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("AAAA", encoding="utf-8")
    read_mtime = _age_read_mtime(f)
    size_at_read = 4
    hash_at_read = content_hash(str(f))
    # equal-length edit (same size, different content)
    f.write_text("BBBB", encoding="utf-8")
    now = time.time()
    os.utime(f, (now, now))
    v = classify_file_freshness(
        str(f), read_mtime, size_at_read=size_at_read, hash_at_read=hash_at_read
    )
    assert v["verdict"] == "stale", v
    assert "content changed" in v["reason"]


def test_no_baseline_hash_same_size_is_stale_safe(tmp_path):
    """Size unchanged but no read-time hash baseline → can't verify → stale
    (safe direction)."""
    f = tmp_path / "d.txt"
    f.write_text("XXXX", encoding="utf-8")
    read_mtime = _age_read_mtime(f)
    f.write_text("YYYY", encoding="utf-8")  # same size, changed
    now = time.time()
    os.utime(f, (now, now))
    v = classify_file_freshness(str(f), read_mtime, size_at_read=4, hash_at_read=None)
    assert v["verdict"] == "stale"


def test_over_cap_not_hashed(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 1024)
    # cap below file size → content_hash returns None
    assert content_hash(str(f), cap_bytes=100) is None
    # under cap → hashes
    assert content_hash(str(f), cap_bytes=4096) is not None


def test_mtime_unchanged_still_fresh_no_size_needed(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("z", encoding="utf-8")
    mt = f.stat().st_mtime
    v = classify_file_freshness(str(f), mt, size_at_read=1)
    assert v["verdict"] == "fresh"
    assert "unchanged" in v["reason"]
