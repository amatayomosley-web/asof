"""News domain pack.

Current-events vocabulary. Reportage staleness becomes urgent for
breaking topics; less so for evergreen reporting.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "breaking",
        r"\b(breaking|developing|happening\s+now|ongoing)\b",
    ),
    (
        "report_says",
        r"\b(report\s+says|reports\s+say|sources?\s+(say|tell)|according\s+to\s+sources)\b",
    ),
    (
        "latest_news",
        r"\b(latest\s+news|breaking\s+news|news\s+(about|on|regarding))\b",
    ),
    (
        "incident_terms",
        r"\b(incident|outage|disruption|controversy|scandal)\b",
    ),
    (
        "press_release",
        r"\b(press\s+release|announcement|statement\s+from)\b",
    ),
]
