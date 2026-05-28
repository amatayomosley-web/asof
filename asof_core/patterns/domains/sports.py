"""Sports domain pack.

Game state, standings, schedules — all of which decay during live events.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "live_state",
        r"\b(score|result|standings|fixture|match\s+result|game\s+result)\b",
    ),
    (
        "schedule",
        r"\b(game\s+time|kickoff|tipoff|first\s+pitch|tee\s+time|schedule\s+for)\b",
    ),
    (
        "season_state",
        r"\b(playoff|championship|standings|division\s+leader|wild\s+card|bracket)\b",
    ),
    (
        "player_state",
        r"\b(injured|active\s+roster|inactive\s+list|game[-\s]time\s+decision|starting\s+lineup)\b",
    ),
    (
        "live_stats",
        r"\b(live\s+score|live\s+stats|in[-\s]game\s+(stats|odds)|halftime|fourth\s+quarter)\b",
    ),
]
