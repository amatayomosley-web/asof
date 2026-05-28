"""Cross-session URL provenance database.

SQLite at ~/.asof/provenance.db.

Schema:
    file_provenance(
        file_path TEXT,           -- absolute path of the file that was written
        url TEXT,                 -- URL fetched during authoring
        fetched_at TEXT,          -- ISO 8601 UTC of the WebFetch
        etag TEXT,                -- captured ETag if available
        last_modified TEXT,       -- captured Last-Modified if available
        session_id TEXT,
        PRIMARY KEY (file_path, url, fetched_at)
    )

Use case: vacation-plan.md scenario. Day 0, the agent WebFetches flight
prices and writes them into vacation-plan.md. Day 60, you reopen the
file. Without provenance, AsOf knows the file is 60 days old but not
where the data came from. With provenance, AsOf can surface "this file
was authored using these URLs on Day 0; consider re-fetching for current
values."

Cairn-internal cairn_watch could surface provenance via a new section
in the watch block. Generic harness consumers query the database
directly via `asof_core.provenance.lookup(file_path)`.

Off by default (silent if SQLite not available). Enabled by:
    ASOF_PROVENANCE=on
or
    config.provenance: true
"""
from __future__ import annotations

import os
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_PATH = Path.home() / ".asof" / "provenance.db"


def _provenance_enabled() -> bool:
    """Check config + env."""
    if os.environ.get("ASOF_PROVENANCE", "").lower() in ("on", "true", "1"):
        return True
    try:
        config_path = Path.home() / ".asof" / "config.json"
        if config_path.is_file():
            with config_path.open(encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("provenance") is True:
                return True
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return False


def _connect() -> Optional[sqlite3.Connection]:
    """Open the provenance DB, creating schema if needed. Returns None
    on any error (silent-fail discipline)."""
    if not _provenance_enabled():
        return None
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_provenance (
                file_path TEXT NOT NULL,
                url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                etag TEXT,
                last_modified TEXT,
                session_id TEXT,
                PRIMARY KEY (file_path, url, fetched_at)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_path ON file_provenance(file_path)"
        )
        conn.commit()
        return conn
    except (sqlite3.Error, OSError):
        return None


def record_fetch(
    *,
    file_path: str,
    url: str,
    session_id: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    fetched_at: Optional[datetime] = None,
) -> None:
    """Record that `url` was fetched during the writing of `file_path`
    in this session.

    Called from post_tool when the substrate WebFetches AND subsequently
    Writes/Edits a file in the same session — the heuristic for
    "this URL contributed to this file's content."

    Silent-fail.
    """
    conn = _connect()
    if conn is None:
        return
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO file_provenance
                (file_path, url, fetched_at, etag, last_modified, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_path,
                url,
                fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                etag,
                last_modified,
                session_id,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def lookup(file_path: str) -> list[dict]:
    """Return all recorded URL fetches associated with `file_path`.

    Used by the watch when a path mention surfaces a file with
    provenance records — the substrate can see "this file was authored
    using these URLs; consider re-verification if their content is
    time-sensitive."
    """
    conn = _connect()
    if conn is None:
        return []
    try:
        cursor = conn.execute(
            """
            SELECT url, fetched_at, etag, last_modified, session_id
            FROM file_provenance
            WHERE file_path = ?
            ORDER BY fetched_at DESC
            """,
            (file_path,),
        )
        rows = cursor.fetchall()
        return [
            {
                "url": r[0],
                "fetched_at": r[1],
                "etag": r[2],
                "last_modified": r[3],
                "session_id": r[4],
            }
            for r in rows
        ]
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def session_fetches(session_id: str) -> list[str]:
    """Return distinct URLs fetched in `session_id`. Used by post_tool
    when a Write/Edit happens to associate recent URL fetches with the
    file being written."""
    conn = _connect()
    if conn is None:
        return []
    try:
        cursor = conn.execute(
            """
            SELECT DISTINCT url FROM file_provenance
            WHERE session_id = ?
            ORDER BY fetched_at DESC
            LIMIT 50
            """,
            (session_id,),
        )
        return [r[0] for r in cursor.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
