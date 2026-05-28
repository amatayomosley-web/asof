"""Domain pattern packs for AsOf — opt-in.

Each pack is a module exporting a PATTERNS list of (name, regex) tuples.
Users enable via config or env var:

    {
      "patterns": {
        "domains": ["finance", "travel"]
      }
    }

Or:

    ASOF_DOMAINS=finance,travel,weather

Available packs (V1):
- finance — general financial vocabulary
- stocks — ticker tracking, P&L, positions
- crypto — crypto-specific terms
- news — breaking events, headline patterns
- travel — fares, availability, bookings
- weather — forecast, conditions
- sports — scores, standings, schedules
- devops — deployments, builds, incidents
"""
