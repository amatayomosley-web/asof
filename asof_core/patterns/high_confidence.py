"""Tier 1 patterns — high-confidence time-sensitivity detectors.

These patterns trigger only when temporal language is bound to dynamic-
content referents. Low false-positive rate. Default ON.

Format: list of (name, regex_string) tuples. Regexes compiled with
re.IGNORECASE in PatternMatcher.

Each pattern catches a specific question shape where time-sensitivity
is unambiguous:
- "current/latest/now/live X" where X is a dynamic noun
- "what's the current X"
- "is X still Y"
- "has X changed since"
- "stock/share price of X"
- "real-time X"
- "today's/yesterday's X" where X is a dynamic noun
"""

HIGH_CONFIDENCE_PATTERNS: list[tuple[str, str]] = [
    (
        "current_dynamic_noun",
        r"\b(current|latest|now|live)\s+(price|rate|version|cost|status|news|forecast|data|figures|quote|fare|value|conditions|level|odds|rating)\b",
    ),
    (
        "whats_the_current",
        r"\bwhat'?s\s+(the\s+)?(current|latest|live|today'?s)\b",
    ),
    (
        "is_X_still",
        r"\bis\s+\w+(\s+\w+){0,3}\s+still\s+(at|in|on|the\s+same|current|active|available|valid|live|open|running|in\s+effect|standing)\b",
    ),
    (
        "has_X_changed",
        r"\bhas\s+\w+(\s+\w+){0,3}\s+changed\s+(since|in|after)\b",
    ),
    (
        "stock_or_share",
        r"\b(stock|share)\s+(price|quote|value)\s+(of|for)\b",
    ),
    (
        "real_time",
        r"\breal[-\s]?time\s+\w+",
    ),
    (
        "up_to_date",
        r"\bup[-\s]?to[-\s]?date\b",
    ),
    (
        "todays_dynamic",
        r"\b(today'?s|yesterday'?s|this\s+(week|month|year)'?s)\s+(price|rate|data|figures|report|update|news|close|open|high|low|forecast)\b",
    ),
    (
        "exchange_rate_or_fx",
        r"\b(exchange\s+rate|fx\s+rate|forex\s+rate|conversion\s+rate)\b",
    ),
    (
        "current_balance_or_balance_of",
        r"\b(current\s+balance|account\s+balance|balance\s+as\s+of)\b",
    ),
]
