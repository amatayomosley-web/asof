"""Stocks domain pack — more aggressive than `finance`.

Catches active-trader vocabulary: position management, intraday metrics,
stop-loss levels, P&L tracking.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "position_or_pnl",
        r"\b(position|pnl|p&l|p/l|drawdown|exposure|allocation)\b",
    ),
    (
        "stop_loss_or_target",
        r"\b(stop[-\s]?loss|take[-\s]?profit|price\s+target|breakeven)\b",
    ),
    (
        "ticker_anywhere",
        r"\$?([A-Z]{2,5})\b(?:\s+(at|@|hit|broke|near))",
    ),
    (
        "intraday",
        r"\b(intraday|premarket|pre[-\s]?market|after[-\s]?hours|opening\s+bell|closing\s+bell)\b",
    ),
    (
        "technical_analysis",
        r"\b(support|resistance|breakout|breakdown|moving\s+average|rsi|macd|vwap)\b",
    ),
    (
        "order_types",
        r"\b(market\s+order|limit\s+order|fill|filled|filled\s+at|order\s+book)\b",
    ),
]
