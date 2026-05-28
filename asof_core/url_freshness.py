"""URL freshness via HEAD requests.

Opt-in tier: full URL re-validation costs network. The hook only fires
HEAD when:
1. The user explicitly enables it in config (`patterns.url_check: true`)
2. The query oracle is invoked on a URL target (asof_query oracle path)

Mechanism: stat-equivalent for the web. Compare ETag / Last-Modified
captured at original WebFetch time (recorded by post_tool in the tool log)
to current values via HEAD. If either has moved, the cached fetch is
stale.

Silent-fail on network errors. The hook NEVER hangs the substrate on
a slow server — short timeouts, no retries.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


# Tight network discipline: HEAD requests run fast or not at all.
HEAD_TIMEOUT_SECONDS = 3.0


def head_request(url: str, *, timeout: float = HEAD_TIMEOUT_SECONDS) -> dict:
    """Issue a HEAD request and capture freshness-relevant headers.

    Returns:
        {
            "ok": bool,                  # True if request succeeded
            "status": int or None,       # HTTP status code
            "etag": str or None,
            "last_modified": str or None,
            "cache_control": str or None,
            "error": str or None,        # short error description if !ok
        }
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "AsOf/0.1.0 (freshness check)")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = resp.headers
            return {
                "ok": True,
                "status": resp.status,
                "etag": headers.get("ETag"),
                "last_modified": headers.get("Last-Modified"),
                "cache_control": headers.get("Cache-Control"),
                "error": None,
            }
    except urllib.error.HTTPError as e:
        # 404, 410, etc. — content is unavailable now; original cache stale
        return {
            "ok": False,
            "status": e.code,
            "etag": None,
            "last_modified": None,
            "cache_control": None,
            "error": f"HTTP {e.code}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "ok": False,
            "status": None,
            "etag": None,
            "last_modified": None,
            "cache_control": None,
            "error": str(e)[:120],
        }


def classify_url_freshness(
    url: str,
    *,
    etag_at_fetch: Optional[str] = None,
    last_modified_at_fetch: Optional[str] = None,
    timeout: float = HEAD_TIMEOUT_SECONDS,
) -> dict:
    """Compare current HEAD response to captured ETag/Last-Modified.

    Args:
        url: the URL to check
        etag_at_fetch: ETag captured at original WebFetch (from tool log)
        last_modified_at_fetch: Last-Modified at original fetch
        timeout: HEAD request timeout

    Returns:
        {
            "verdict": "fresh" | "stale" | "unverifiable" | "gone",
            "reason": short string,
            "current_etag": str or None,
            "current_last_modified": str or None,
        }
    """
    h = head_request(url, timeout=timeout)
    if not h["ok"]:
        if h["status"] in (404, 410):
            return {
                "verdict": "gone",
                "reason": f"server returned {h['status']}; resource no longer available",
                "current_etag": None,
                "current_last_modified": None,
            }
        return {
            "verdict": "unverifiable",
            "reason": f"HEAD request failed: {h.get('error') or 'unknown'}",
            "current_etag": None,
            "current_last_modified": None,
        }

    # Compare ETag (strong signal)
    if etag_at_fetch and h["etag"]:
        if etag_at_fetch == h["etag"]:
            return {
                "verdict": "fresh",
                "reason": "ETag unchanged since original fetch",
                "current_etag": h["etag"],
                "current_last_modified": h["last_modified"],
            }
        return {
            "verdict": "stale",
            "reason": f"ETag changed: was {etag_at_fetch}, now {h['etag']}",
            "current_etag": h["etag"],
            "current_last_modified": h["last_modified"],
        }

    # Fall back to Last-Modified (weaker but common)
    if last_modified_at_fetch and h["last_modified"]:
        if last_modified_at_fetch == h["last_modified"]:
            return {
                "verdict": "fresh",
                "reason": "Last-Modified unchanged since original fetch",
                "current_etag": h["etag"],
                "current_last_modified": h["last_modified"],
            }
        return {
            "verdict": "stale",
            "reason": f"Last-Modified changed: was {last_modified_at_fetch}, now {h['last_modified']}",
            "current_etag": h["etag"],
            "current_last_modified": h["last_modified"],
        }

    # Neither header was captured originally — can't compare
    return {
        "verdict": "unverifiable",
        "reason": "no ETag/Last-Modified captured at original fetch; cannot compare",
        "current_etag": h["etag"],
        "current_last_modified": h["last_modified"],
    }
