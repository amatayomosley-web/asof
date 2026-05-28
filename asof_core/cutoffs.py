"""Training-cutoff lookup table for AsOf.

Maps model identifiers to their published training-data cutoff dates.
Used to compute "your knowledge is N months old" framing without asking
the model to do the arithmetic itself.

Sources of truth:
- Anthropic model docs (https://docs.anthropic.com)
- Google Gemini model docs
- OpenAI model docs
- Other vendors' published model cards

Maintenance: this table needs occasional updates as new models ship.
Process: when a new model ID appears in production usage, check the
vendor's published cutoff date and add the entry. Default fallback for
unknown models is None (the hook surfaces 'unknown cutoff — assume
time-sensitive claims may be stale').

User override: ASOF_TRAINING_CUTOFF environment variable takes precedence
over the table lookup. Format: YYYY-MM (e.g., "2026-01").
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional


# Cutoff format: "YYYY-MM" — first-of-month is used for arithmetic.
TRAINING_CUTOFFS: dict[str, str] = {
    # Anthropic — Claude family
    "claude-opus-4-8": "2026-01",        # released 2026-05-28; training cutoff Jan 2026 per platform.claude.com models doc
    "claude-opus-4-8[1m]": "2026-01",
    "claude-opus-4-7": "2026-01",
    "claude-opus-4-7[1m]": "2026-01",
    "claude-sonnet-4-6": "2026-01",
    "claude-haiku-4-5": "2025-10",
    "claude-haiku-4-5-20251001": "2025-10",
    "claude-opus-4-6": "2025-09",
    "claude-sonnet-4-20250514": "2025-04",
    "claude-3-5-sonnet-20241022": "2024-04",
    "claude-3-5-haiku-20241022": "2024-07",
    "claude-3-opus-20240229": "2023-08",
    # Google — Gemini family
    "gemini-3.5-flash": "2026-01",   # released 2026-05-19, cutoff Jan 2026 (Current runs this)
    "gemini-2.5-pro": "2025-04",
    "gemini-2.5-flash": "2025-04",
    "gemini-2.0-flash": "2024-08",
    "gemini-1.5-pro": "2024-05",
    # OpenAI — GPT family (best-known cutoffs)
    "gpt-4o": "2023-10",
    "gpt-4o-2024-08-06": "2023-10",
    "gpt-4-turbo": "2023-12",
    "gpt-4": "2023-04",
    "gpt-3.5-turbo": "2021-09",
    # Meta — Llama family
    "llama-3-70b": "2023-12",
    "llama-3.1-405b": "2023-12",
    # Mistral
    "mistral-large": "2023-12",
    "mistral-small": "2023-10",          # per modelfile SYSTEM directive on Ollama mistral-small:latest
    "mistral-small-3": "2023-10",
    "mistral-nemo": "2024-04",           # NVIDIA partnership instruct; verify against vendor docs
    # Google — Gemma family (open weights)
    "gemma4-e4b": "2024-06",             # approximate; verify against Google AI Gemma 4 model card
    "gemma4-e2b": "2024-06",             # approximate; verify against Google AI Gemma 4 model card
    "gemma-3-27b-it": "2024-08",         # approximate; verify against vendor docs
    # DeepSeek
    "deepseek-r1-distill-qwen-32b": "2024-09",  # based on Qwen2.5-32B base; verify
    "deepseek-r1": "2024-09",            # prefix-match fallback for ollama "deepseek-r1:32b" form
}


def lookup_cutoff(model_id: str) -> Optional[str]:
    """Return the cutoff string for a model ID, or None if unknown.

    Resolution order:
    1. ASOF_TRAINING_CUTOFF env var (user override, takes precedence)
    2. Exact match in TRAINING_CUTOFFS
    3. Prefix match (handles versioned IDs like 'claude-opus-4-7-20260115')
    4. None
    """
    override = os.environ.get("ASOF_TRAINING_CUTOFF")
    if override:
        return override.strip()

    if model_id in TRAINING_CUTOFFS:
        return TRAINING_CUTOFFS[model_id]

    # Prefix match — try progressively shorter prefixes
    for known_id in TRAINING_CUTOFFS:
        if model_id.startswith(known_id):
            return TRAINING_CUTOFFS[known_id]
    for known_id in TRAINING_CUTOFFS:
        if known_id.startswith(model_id):
            return TRAINING_CUTOFFS[known_id]

    return None


def cutoff_to_date(cutoff: str) -> date:
    """Convert a 'YYYY-MM' or 'YYYY-MM-DD' cutoff string to a date.

    Assumes first-of-month if only year-month given.
    """
    parts = cutoff.split("-")
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1)
    if len(parts) == 3:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError(f"cutoff must be YYYY-MM or YYYY-MM-DD, got {cutoff!r}")


def gap_to_now(cutoff: str, *, now: Optional[date] = None) -> dict:
    """Compute the gap from a training cutoff to now.

    Returns a dict with:
    - days: total days elapsed
    - months: approximate months (days / 30)
    - human: a human-readable phrase ('4 months ago', '2 years 3 months ago')
    """
    if now is None:
        now = datetime.now(timezone.utc).date()
    cutoff_d = cutoff_to_date(cutoff)
    days = (now - cutoff_d).days
    if days < 0:
        return {"days": days, "months": 0, "human": "(cutoff is in the future)"}
    months = days // 30
    years = months // 12
    rem_months = months % 12

    if years >= 1:
        if rem_months > 0:
            human = f"{years} year{'s' if years > 1 else ''} {rem_months} month{'s' if rem_months > 1 else ''} ago"
        else:
            human = f"{years} year{'s' if years > 1 else ''} ago"
    elif months >= 1:
        human = f"~{months} month{'s' if months > 1 else ''} ago"
    elif days >= 7:
        weeks = days // 7
        human = f"~{weeks} week{'s' if weeks > 1 else ''} ago"
    else:
        human = f"{days} day{'s' if days != 1 else ''} ago"

    return {"days": days, "months": months, "human": human}
