"""Tests for the surfacing policy: first-surface, suppress-repeat, heartbeat,
new-delta re-surface, and working-set gating."""
from __future__ import annotations

from asof_core import surfacing


def _stale(path: str, mtime: float) -> dict:
    return {"path": path, "reason": "mtime moved", "verdict": "stale",
            "current_mtime": mtime}


def _run_turn(state, turn, stale_files, accessed=None, heartbeat=12, working_set=15):
    return surfacing.decide_surfacing(
        stale_files, state, turn, accessed or set(),
        heartbeat=heartbeat, working_set=working_set,
    )


def test_first_surface_then_suppress_then_heartbeat():
    state = {"turn": 0, "files": {}}
    f = _stale("/x/budget.csv", 100.0)

    # Turn 1: first-surface — appears.
    out = _run_turn(state, 1, [f])
    assert [s["path"] for s in out] == ["/x/budget.csv"], "first-surface should appear"

    # Turns 2..12: suppressed (same mtime, within heartbeat window, no re-access).
    for t in range(2, 13):
        out = _run_turn(state, t, [f])
        assert out == [], f"turn {t} should be suppressed"

    # Turn 13: heartbeat due (13 - 1 = 12 >= 12) but only fires if in working set.
    # last_access_turn was set to 1 at first-surface; 13 - 1 = 12 <= 15 → in set.
    out = _run_turn(state, 13, [f])
    assert [s["path"] for s in out] == ["/x/budget.csv"], "heartbeat should re-surface in working set"


def test_new_delta_resurfaces_immediately():
    state = {"turn": 0, "files": {}}
    out = _run_turn(state, 1, [_stale("/x/a.txt", 100.0)])
    assert len(out) == 1  # first-surface

    # Turn 2: same file, NEW mtime (changed again) → immediate re-surface.
    out = _run_turn(state, 2, [_stale("/x/a.txt", 200.0)])
    assert len(out) == 1, "a fresh mtime change must re-surface immediately"


def test_leaves_working_set_goes_quiet():
    """A file never re-accessed falls out of the working set; once past the
    window, the heartbeat stops even when staleness persists."""
    state = {"turn": 0, "files": {}}
    f = _stale("/x/cold.md", 100.0)
    _run_turn(state, 1, [f])                       # first-surface @1, access@1
    # Heartbeat at 13 fires (12 <= 15 working set).
    out13 = _run_turn(state, 13, [f])
    assert len(out13) == 1
    # Next heartbeat would be ~25; access still @1 → 25-1=24 > 15 → out of set.
    for t in range(14, 26):
        out = _run_turn(state, t, [f])
        assert out == [], f"cold file should stay quiet at turn {t}"


def test_reaccess_refreshes_working_set():
    state = {"turn": 0, "files": {}}
    f = _stale("/x/doc.md", 100.0)
    _run_turn(state, 1, [f])                        # first-surface
    # Re-access at turn 20 (path touched) refreshes working set.
    out = _run_turn(state, 20, [f], accessed={"/x/doc.md"})
    # 20 - last_surfaced(1) = 19 >= 12 heartbeat, and access just refreshed to 20
    # → in working set → re-surfaces.
    assert len(out) == 1, "re-access should refresh working set and allow heartbeat"


def test_state_roundtrip(tmp_path):
    sid = "rt"
    state = {"turn": 5, "last_watch_ts": 123.0, "files": {"/a": {"last_surfaced_turn": 5}}}
    surfacing.save_state(sid, state, state_dir=tmp_path)
    loaded = surfacing.load_state(sid, state_dir=tmp_path)
    assert loaded["turn"] == 5
    assert loaded["files"]["/a"]["last_surfaced_turn"] == 5


def test_load_state_missing_returns_skeleton(tmp_path):
    s = surfacing.load_state("nope", state_dir=tmp_path)
    assert s == {"turn": 0, "last_watch_ts": None, "files": {}}
