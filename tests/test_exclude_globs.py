"""Tests for Class B staleness exclusion: config-driven exclude_globs drop
transient/agent-owned Read paths (e.g. background-task .output files) from
the working-set staleness scan."""
from __future__ import annotations

import os
import time

from asof_core.hooks.watch import _evaluate_working_set, _excluded_from_staleness


def _make_stale(path, *, seconds_ago=100.0):
    """Write `path`, return a read-record whose mtime_at_read is in the past
    and whose recorded size no longer matches the (grown) file on disk — so
    classify_file_freshness yields a confident 'size changed' stale verdict."""
    path.write_text("seed", encoding="utf-8")
    read_mtime = time.time() - seconds_ago
    size_at_read = 4
    # grow the file; write_text bumps mtime to ~now (clearly after read_mtime)
    path.write_text("grown content, larger now", encoding="utf-8")
    return {
        "tool_name": "Read",
        "input_summary": str(path),
        "mtime_at_read": read_mtime,
        "size_bytes": size_at_read,
    }


def test_excluded_glob_suppresses_stale_but_others_still_surface(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    excluded = tasks / "bz64j655n.output"
    normal = tmp_path / "notes.md"

    records = [_make_stale(excluded), _make_stale(normal)]

    stale = _evaluate_working_set(records, exclude_globs=["*/tasks/*.output"])
    paths = {s["path"] for s in stale}

    assert str(normal) in paths, "non-matching stale file must still surface"
    assert str(excluded) not in paths, "path matching exclude_glob must not surface"


def test_no_exclude_globs_tracks_everything(tmp_path):
    """Empty/absent exclude list = prior behavior (additive, no migration)."""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    a = tasks / "job.output"
    b = tmp_path / "notes.md"

    records = [_make_stale(a), _make_stale(b)]

    stale_default = _evaluate_working_set(records)
    stale_empty = _evaluate_working_set(records, exclude_globs=[])

    for result in (stale_default, stale_empty):
        paths = {s["path"] for s in result}
        assert str(a) in paths
        assert str(b) in paths


def test_windows_backslash_path_matches_forward_slash_glob():
    """The live Windows path uses backslashes; the seeded glob uses forward
    slashes. Slash-normalized matching must bridge them."""
    win = (
        r"C:\Users\willi\AppData\Local\Temp\claude"
        r"\C--Users-willi\175a9720\tasks\bz64j655n.output"
    )
    assert _excluded_from_staleness(win, ["*/claude/*/tasks/*.output"])
    assert not _excluded_from_staleness(
        r"C:\Users\willi\cairn\projects\asof\notes.md",
        ["*/claude/*/tasks/*.output"],
    )
