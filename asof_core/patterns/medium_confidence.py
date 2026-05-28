"""Tier 2 patterns — medium-confidence time-sensitivity detectors.

Broader temporal flags. Higher false-positive rate than Tier 1 but
still bounded. Default ON.

Catches:
- Recency markers (recently, lately)
- Temporal scope words (yesterday, today, tomorrow) when bound to action
- Relative-time phrases (last week, next month, N days ago)
- Forward-looking time (forecast, projection, estimate)
- Time-bounded (deadline, expires, due)
- Time-pinned (schedule, booking, reservation)
- Temporal questions (when, how long ago)
"""

MEDIUM_CONFIDENCE_PATTERNS: list[tuple[str, str]] = [
    (
        "recency_markers",
        r"\b(recently|lately|just\s+now|moments\s+ago)\b",
    ),
    (
        "day_words",
        r"\b(yesterday|today|tomorrow|tonight)\b",
    ),
    (
        "relative_period",
        r"\b(last|next|this|past|coming|upcoming)\s+(week|month|year|quarter|day|hour|night|morning|afternoon|evening|weekend|fiscal\s+year|business\s+day)\b",
    ),
    (
        "n_units_ago",
        r"\b\d+\s+(seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b",
    ),
    (
        "n_units_from_now",
        r"\b(in\s+)?\d+\s+(seconds?|minutes?|hours?|days?|weeks?|months?|years?)(\s+from\s+now|\s+from\s+today)?\b",
    ),
    (
        "fiscal_period",
        r"\b(this|next|last|current)\s+(month|quarter|year|fiscal\s+year|fiscal\s+quarter)\b",
    ),
    (
        "forward_looking",
        r"\b(forecast|projection|projected|estimate|estimated|prediction|predicted|outlook|guidance)\b",
    ),
    (
        "time_bounded",
        r"\b(deadline|expires?|expiring|expired|due\s+date|due\s+by|cutoff|sunset)\b",
    ),
    (
        "time_pinned",
        r"\b(schedule|scheduled|booking|booked|reservation|reserved|appointment|meeting)\s+(for|on|at)?\b",
    ),
    (
        "temporal_question",
        r"\b(when|how\s+long\s+ago|how\s+recent|how\s+old)\b",
    ),
    (
        "duration_questions",
        r"\b(how\s+long\s+(does|will|did|has)|how\s+(many|much)\s+time)\b",
    ),
    (
        "ongoing_state",
        r"\b(still\s+working|still\s+running|still\s+open|still\s+active|still\s+valid)\b",
    ),
    (
        "version_recency",
        r"\b(newest\s+version|latest\s+version|most\s+recent\s+version|current\s+version)\b",
    ),
]
