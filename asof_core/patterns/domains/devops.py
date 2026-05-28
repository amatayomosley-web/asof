"""DevOps domain pack.

Deployment and infrastructure state. Use with care — many of these
words appear in coding contexts where staleness isn't the question.

Suggested: enable only for sessions where the user is actively managing
production state, not for general coding work.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "deployment_state",
        r"\b(deployed|in\s+production|live\s+on|prod\s+state|prod\s+version)\b",
    ),
    (
        "release_or_build",
        r"\b(release\s+number|build\s+status|build\s+number|tagged\s+release|deploy\s+id)\b",
    ),
    (
        "incident_state",
        r"\b(incident|outage\s+status|uptime|sla\s+status|on[-\s]call)\b",
    ),
    (
        "monitoring",
        r"\b(error\s+rate|latency|throughput|qps|rps)\s+(now|current|currently|today)\b",
    ),
    (
        "rollout",
        r"\b(canary|rolling\s+deploy|blue[-\s]green|feature\s+flag\s+state)\b",
    ),
]
