"""Build battery.jsonl — the 11-prompt A/B test battery with pre-computed AsOf verdicts.

Each row contains:
  - prompt_id, category, prompt_text
  - verdict_default: the asof_watch output assuming generic (unknown) cutoff
  - verdict_with_cutoff_<model>: per-model verdict with that model's specific cutoff

Run this script once to generate battery.jsonl. The runner reads from it.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# asoftime is installed dev-mode; asof_core is importable.
from asof_core.hooks.watch import watch
from asof_core.cutoffs import lookup_cutoff, gap_to_now


REPO_ROOT = Path(__file__).resolve().parents[2]
BATTERY_PATH = REPO_ROOT / "tests" / "abtests" / "battery.jsonl"


# Lean primer for OSS models (8K context budget). Compressed from claude_code/SKILL.md.
LEAN_PRIMER = """You have access to AsOf — a temporal-awareness hook that pre-computes freshness verdicts and surfaces them to you in an `=== AsOf ===` block.

What the block tells you and what to do with it:

1. `## File freshness (this session)` — files you Read earlier whose mtime has moved since. STALE means re-read before grounding any claim on that file's content.

2. `## Files referenced in your message` — paths in the user's prompt with their current mtime. Use the age fact to decide whether to trust the user's snapshot or re-fetch.

3. `## Timestamps in your message` — the hook parsed dates/quarters/relative-time in the prompt and pre-computed the gap to today. **Do not redo this date arithmetic.** Apply the result. If the gap says "Q3 2025 → 6 months ago," surface that two earnings cycles have passed before projecting forward.

4. `## Time-sensitive phrasing detected` — language suggesting time-decay matters. Hedge against training-cutoff staleness; suggest the user verify current values.

5. `## Training cutoff` — your model's training cutoff and the gap to today. Anything time-sensitive newer than the cutoff is unknown to you — don't guess.

The principle: **the hook does the math, you apply the verdict.** You are unreliable at multi-step date arithmetic. The hook is reliable. When you see "147 days ago" in the block, that's computed precisely — use it.

Most prompts produce NO AsOf block. Silence means nothing time-sensitive triggered. When a block IS present, it has been triaged for actionable signal."""


# The 11 prompts.
PROMPTS = [
    # Refuse-vs-Compute: model should refuse / hedge instead of guessing time-sensitive fact
    {"id": "P1", "category": "refuse-vs-compute",
     "prompt": "What's the current best Python version for ML workloads?"},
    {"id": "P2", "category": "refuse-vs-compute",
     "prompt": "What's a typical 4-star hotel rate in central Paris in summer?"},
    # Stale-vs-Live anchor: arithmetic must anchor to today, not training cutoff
    {"id": "P3", "category": "stale-vs-live",
     "prompt": "I deposited $10,000 on March 15, 2024 at 4.5% APR compounded monthly. What's my balance today?"},
    {"id": "P4", "category": "stale-vs-live",
     "prompt": "My subscription started June 1, 2025 on a 12-month plan. Is it still active?"},
    # Cached-vs-Recheck: simulated cached file with external edit
    {"id": "P5", "category": "cached-vs-recheck",
     "prompt": "You read budget.csv 30 minutes ago. The content was: 'Office supplies: $1200, Software: $800, Travel: $2400, Total: $4400'. The user has since edited the file externally. They ask: 'Is the total still under $5,000?'"},
    # Pre-computed gap: dated facts where elapsed time is the load-bearing variable
    {"id": "P6", "category": "pre-computed-gap",
     "prompt": "Here's NVDA's Q3 2025 earnings (reported November 2025): Revenue $35.1B, EPS $0.81, data center segment +94% YoY. Project their next quarter's earnings."},
    {"id": "P7", "category": "pre-computed-gap",
     "prompt": "US inflation hit 3.4% in 2024. Has it changed meaningfully since?"},
    # Static-vs-Versioned: software API versions, named-year events
    {"id": "P8", "category": "static-vs-versioned",
     "prompt": "Here's my Python code: `from openai import OpenAI; client = OpenAI(api_key='sk-...')`. Why is `openai.ChatCompletion.create()` not working?"},
    {"id": "P9", "category": "static-vs-versioned",
     "prompt": "When does WWDC 2026 happen?"},
    # Controls: AsOf should NOT change behavior
    {"id": "P10", "category": "control",
     "prompt": "What is 247 times 89?"},
    {"id": "P11", "category": "control",
     "prompt": "Write a 3-line haiku about thunder."},
]


# Models to pre-compute per-cutoff verdicts for. None = generic/unknown-cutoff condition.
CUTOFF_VARIANTS = {
    "default": None,
    "gemma4-e4b": "2024-06",
    "mistral-small": "2023-10",
    "deepseek-r1": "2024-09",
    "claude-opus-4-7": "2026-01",
    "claude-sonnet-4-6": "2026-01",
    "claude-haiku-4-5": "2025-10",
}


def make_verdict(prompt_text: str, cutoff_override: str | None, now: datetime) -> str:
    """Run asof_watch against the prompt with a specific cutoff context."""
    if cutoff_override:
        os.environ["ASOF_TRAINING_CUTOFF"] = cutoff_override
    else:
        os.environ.pop("ASOF_TRAINING_CUTOFF", None)
    # Use an isolated session_id so prior tool logs don't bleed in.
    session_id = "abtest-battery-build"
    return watch(session_id=session_id, prompt_text=prompt_text, now=now)


def main():
    now = datetime.now(timezone.utc)
    rows = []
    for p in PROMPTS:
        row = {
            "id": p["id"],
            "category": p["category"],
            "prompt": p["prompt"],
            "primer": LEAN_PRIMER,
            "verdicts": {},
        }
        for variant, cutoff in CUTOFF_VARIANTS.items():
            v = make_verdict(p["prompt"], cutoff, now)
            row["verdicts"][variant] = v
        rows.append(row)

    BATTERY_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {BATTERY_PATH}")

    # Summary
    print()
    print("Verdict presence by prompt (default cutoff):")
    for r in rows:
        v = r["verdicts"]["default"]
        present = "YES" if v.strip() else "no"
        first_line = v.strip().split("\n", 1)[0] if v.strip() else ""
        print(f"  {r['id']:4s} {r['category']:22s} verdict={present:3s}  {first_line[:60]}")


if __name__ == "__main__":
    main()
