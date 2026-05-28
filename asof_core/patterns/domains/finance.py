"""Finance domain pack.

General financial vocabulary that warrants temporal grounding. Catches
analyst-flavored questions where staleness is high-stakes (investment
decisions, position sizing, valuation).
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "ticker_with_price_or_quote",
        r"\b([A-Z]{2,5})\b\s+(price|quote|value|close|open|high|low|target)\b",
    ),
    (
        "buy_or_sell",
        r"\b(buy|sell|long|short|enter|exit)\s+(position|at|here|now|the)\b",
    ),
    (
        "earnings_or_dividend",
        r"\b(earnings|dividend|yield|eps|revenue|guidance)\s+(of|for|this|next|last)\b",
    ),
    (
        "futures_options",
        r"\b(futures|options|calls|puts|expiry|strike)\b",
    ),
    (
        "fundamentals",
        r"\b(p/e|pe\s+ratio|market\s+cap|book\s+value|free\s+cash\s+flow|fcf)\b",
    ),
    (
        "macro",
        r"\b(fed\s+rate|interest\s+rate|cpi|inflation|gdp|unemployment)\s+(rate|number|figure|data)\b",
    ),
]
