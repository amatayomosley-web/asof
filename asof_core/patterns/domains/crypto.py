"""Crypto domain pack.

Crypto-specific vocabulary. Markets are 24/7 so staleness signals
matter on shorter time scales than traditional equities.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "common_tickers",
        r"\b(BTC|ETH|SOL|ADA|XRP|DOGE|AVAX|MATIC|LINK|DOT)\b",
    ),
    (
        "price_action",
        r"\b(price\s+action|pump|dump|rug|moon|to\s+the\s+moon|bear|bull)\b",
    ),
    (
        "market_terms",
        r"\b(market\s+cap|mcap|circulating\s+supply|max\s+supply|halving|whale\s+movement)\b",
    ),
    (
        "defi_terms",
        r"\b(yield\s+farming|liquidity\s+pool|lp\s+tokens?|apy|apr|tvl|gas\s+fees)\b",
    ),
    (
        "stablecoin_or_peg",
        r"\b(usdc|usdt|dai|stablecoin|peg|depeg)\b",
    ),
]
