"""Timestamp parser for user prompts.

Detects timestamps embedded in user-pasted content and pre-computes the
gap to current UTC in Python. The hook emits the result as text; the
model reads the pre-computed gap and never does date arithmetic itself.

Addresses Naomi's RT finding (LLM arithmetic instability): boundary
cases like "147 days ago" or end-of-month/leap-year computations fail
in chat. Python's datetime gets them right reliably.

Detection covers:
- ISO 8601 dates: 2026-05-24, 2026-05-24T16:00:00Z
- US-style: 5/24/2026, 05-24-2026
- Long-form: November 2025, 24 November 2025
- Quarters/fiscal: Q3 2025, FY2025, Q4 FY26
- Relative phrases: yesterday, last week, three days ago, two months ago
- Quoted timestamps: "2026-05-24"
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional


# Quarterly announcement-window heuristic. Tech earnings typically:
# Q1 → late April / early May
# Q2 → late July / early August
# Q3 → late October / early November
# Q4 → late January / mid-February (of the following year)
# This is used for quarter→announcement-date estimation when no specific
# date is available.
QUARTER_ANNOUNCE_MONTHS = {
    1: 5,   # Q1 announces in May
    2: 8,   # Q2 in August
    3: 11,  # Q3 in November
    4: 2,   # Q4 in February of next year
}


def _import_dateparser():
    """Lazy-import dateparser so it's only required when natural-language
    parsing is needed. Some users may prefer a regex-only build to avoid
    the dependency footprint."""
    try:
        import dateparser
        return dateparser
    except ImportError:
        return None


def parse_iso(text: str) -> Optional[date]:
    """Parse an ISO 8601 date or datetime string. Returns None on failure."""
    text = text.strip()
    # Try YYYY-MM-DD first
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def parse_quarter(text: str) -> Optional[dict]:
    """Parse a quarter reference. Returns a dict:
        {"year": int, "quarter": int, "announce_date": date}
    or None if no match.

    Handles: "Q3 2025", "Q3 FY2025", "FY2025 Q3", "Q3'25", "third quarter 2025"
    """
    text = text.strip()

    # Q<N> <YEAR> or Q<N>FY<YEAR>
    m = re.search(r"\bQ([1-4])\s*(?:FY)?\s*(\d{4})\b", text, re.IGNORECASE)
    if not m:
        # Q<N>'<YY>
        m = re.search(r"\bQ([1-4])\s*['’](\d{2})\b", text, re.IGNORECASE)
        if m:
            year = 2000 + int(m.group(2))
            quarter = int(m.group(1))
        else:
            # "third quarter 2025"
            m = re.search(r"\b(first|second|third|fourth)\s+quarter\s+(\d{4})\b", text, re.IGNORECASE)
            if m:
                ord_map = {"first": 1, "second": 2, "third": 3, "fourth": 4}
                quarter = ord_map[m.group(1).lower()]
                year = int(m.group(2))
            else:
                return None
    else:
        quarter = int(m.group(1))
        year = int(m.group(2))

    announce_month = QUARTER_ANNOUNCE_MONTHS[quarter]
    announce_year = year if quarter != 4 else year + 1
    try:
        announce_date = date(announce_year, announce_month, 15)
    except ValueError:
        return None
    return {
        "year": year,
        "quarter": quarter,
        "announce_date": announce_date,
    }


def parse_natural(text: str, *, base_date: Optional[date] = None) -> Optional[date]:
    """Parse a natural-language date string ("yesterday", "last week",
    "three days ago", "last Tuesday") into an absolute date.

    Returns None if dateparser is unavailable or the string doesn't parse.
    """
    dp = _import_dateparser()
    if dp is None:
        return None
    base = base_date or datetime.now(timezone.utc).date()
    try:
        result = dp.parse(
            text,
            settings={
                "RELATIVE_BASE": datetime.combine(base, datetime.min.time()),
                "PREFER_DATES_FROM": "past",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
        if result is None:
            return None
        return result.date()
    except (ValueError, TypeError):
        return None


def find_timestamps(text: str, *, base_date: Optional[date] = None) -> list[dict]:
    """Find all timestamp-like references in `text` and resolve each to
    an absolute date plus a pre-computed gap to current UTC.

    Returns a list of dicts:
        {
            "raw": original string from text,
            "resolved": date object,
            "kind": "iso" | "quarter" | "relative" | "natural",
            "gap_days": int,
            "gap_human": str,
            "context": for quarters, additional info (year, quarter, announce_date)
        }
    """
    if base_date is None:
        base_date = datetime.now(timezone.utc).date()

    found: list[dict] = []
    seen_spans: list[tuple[int, int]] = []

    def _add(raw: str, resolved: date, kind: str, span: tuple[int, int], **extra) -> None:
        # Skip if this span overlaps an already-found one (prevents
        # duplicate matches like "Q3 2025" being caught by both quarter
        # and natural parsers)
        for s, e in seen_spans:
            if not (span[1] <= s or span[0] >= e):
                return
        seen_spans.append(span)
        gap_days = (base_date - resolved).days
        found.append({
            "raw": raw,
            "resolved": resolved,
            "kind": kind,
            "gap_days": gap_days,
            "gap_human": humanize_gap(gap_days),
            **extra,
        })

    # ISO dates
    for m in re.finditer(r"\b(\d{4}-\d{1,2}-\d{1,2})\b", text):
        d = parse_iso(m.group(1))
        if d:
            _add(m.group(1), d, "iso", m.span())

    # Quarters
    for pattern in [
        r"\bQ[1-4]\s*(?:FY)?\s*\d{4}\b",
        r"\bQ[1-4]\s*['’]\d{2}\b",
        r"\b(?:first|second|third|fourth)\s+quarter\s+\d{4}\b",
    ]:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            q = parse_quarter(m.group(0))
            if q:
                _add(
                    m.group(0),
                    q["announce_date"],
                    "quarter",
                    m.span(),
                    year=q["year"],
                    quarter=q["quarter"],
                )

    # Relative phrases (handled by dateparser, scoped to common shapes)
    relative_patterns = [
        r"\b(yesterday|today|tomorrow)\b",
        r"\b(last|next|this)\s+(week|month|year|quarter|fiscal\s+year)\b",
        r"\b\d+\s+(days?|weeks?|months?|years?|hours?|minutes?)\s+ago\b",
        r"\b(last|next)\s+(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b",
    ]
    for pattern in relative_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            d = parse_natural(m.group(0), base_date=base_date)
            if d:
                _add(m.group(0), d, "relative", m.span())

    # Long-form dates (e.g., "November 2025", "24 November 2025")
    long_pattern = (
        r"\b(?:\d{1,2}\s+)?"
        r"(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"(?:\s+\d{1,2})?(?:,?\s+\d{4})?\b"
    )
    for m in re.finditer(long_pattern, text):
        d = parse_natural(m.group(0), base_date=base_date)
        if d:
            _add(m.group(0), d, "natural", m.span())

    # Sort by appearance order in text
    found.sort(key=lambda r: text.find(r["raw"]))
    return found


def humanize_gap(days: int) -> str:
    """Render a day-count as a human-readable phrase.

    Negative days = in the future.
    """
    if days == 0:
        return "today"
    if days < 0:
        days = -days
        suffix = "from now"
    else:
        suffix = "ago"

    if days == 1:
        return f"1 day {suffix}"
    if days < 7:
        return f"{days} days {suffix}"
    if days < 30:
        weeks = days // 7
        return f"~{weeks} week{'s' if weeks > 1 else ''} {suffix}"
    if days < 365:
        months = days // 30
        return f"~{months} month{'s' if months > 1 else ''} {suffix}"
    years = days // 365
    remainder_months = (days % 365) // 30
    if remainder_months > 0:
        return f"{years} year{'s' if years > 1 else ''} {remainder_months} month{'s' if remainder_months > 1 else ''} {suffix}"
    return f"{years} year{'s' if years > 1 else ''} {suffix}"


def quarters_since_announcement(quarter_info: dict, *, base_date: Optional[date] = None) -> int:
    """Given a parsed quarter, how many quarters have ended since its
    announcement? Used to surface 'two earnings cycles have happened
    since' for financial content."""
    if base_date is None:
        base_date = datetime.now(timezone.utc).date()
    announce = quarter_info.get("announce_date")
    if not announce:
        return 0
    days_since = (base_date - announce).days
    return max(0, days_since // 90)  # ~90 days per quarter
