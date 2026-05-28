"""Weather domain pack.

Forecast and current-conditions vocabulary. Decays fastest of any
content type — even minutes-old data may be stale for active weather.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "forecast_terms",
        r"\b(forecast|temperature|precipitation|wind\s+speed|humidity|barometric)\b",
    ),
    (
        "conditions",
        r"\b(weather\s+conditions|current\s+conditions|today'?s\s+weather|tomorrow'?s\s+weather)\b",
    ),
    (
        "extreme_weather",
        r"\b(storm|hurricane|tornado|blizzard|heat\s+wave|cold\s+front|warning|advisory)\b",
    ),
    (
        "outlook",
        r"\b(weather\s+outlook|extended\s+forecast|10[-\s]day|weekly\s+forecast)\b",
    ),
]
