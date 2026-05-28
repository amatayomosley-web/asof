---
name: asof
description: Temporal awareness for Claude. Surfaces freshness verdicts on in-context data — files Read this session, dated content in user prompts, training-cutoff awareness — so stale information doesn't poison fresh reasoning. The hook pre-computes everything in Python; you read pre-computed verdicts and apply them. Never compute date arithmetic in chat — query asof_query if you need a specific gap and the hook hasn't surfaced it.
asof_min_version: "0.1.0"
---

# AsOf — Temporal Awareness

You have access to a hook that captures temporal facts about your work and surfaces them in your context. This skill teaches you how to interpret what it gives you.

## What the hook gives you

### At session start

A directive block with:
- Current date and time (your local TZ)
- Your training cutoff and the gap to current ("January 2026, ~4 months ago")
- This skill's prose, reminding you of the discipline below

### Per turn (when something matters)

A `=== AsOf ===` block, present **only when there is an actionable signal**. Possible sections:

- **`## File freshness (this session)`** — files you Read earlier in this session whose mtime has moved since. Each entry is pre-classified `STALE` with reason.
- **`## Files referenced in your message`** — paths mentioned in the user's prompt, with their current mtime as a fact.
- **`## Timestamps in your message`** — dates, quarters, or relative-time phrases detected in the user's prompt, each with a pre-computed gap ("Q3 2025 → ~6 months ago, announced ~mid-Nov 2025").
- **`## Watchlist`** — opt-in files whose state has changed since the last turn.
- **`## Time-sensitive phrasing detected`** — patterns that suggest time-decay matters for this response.

**Most turns produce no `=== AsOf ===` block.** That's by design. Silence means nothing actionable triggered. Speak ONLY surfaces what needs your attention.

## What to do with each section

### `## File freshness — STALE`

A file you Read earlier has been modified externally since (peer Claude, the user editing in another tool, scheduled task, etc.). Your in-context copy is **not authoritative**.

**Required action:** Re-read the file before grounding any claim or edit on it. The mtime moved; the content may have moved with it.

```
## File freshness (this session)
  STALE  /project/auth.py  mtime moved 2h15m after read, no matching self-write
```

→ Re-read `auth.py` before responding to questions about it or editing it.

### `## Files referenced in your message`

The user mentioned paths. The hook stat'd them and surfaced their mtime as a fact. You can use this to:
- Know how recently the file was modified before deciding whether to Read it
- Answer "when was X last changed?" without Reading (the answer is in the block)
- Decide whether the user's reference is to current state or to a snapshot they captured earlier

```
## Files referenced in your message
  /project/config.yaml    modified 12 days ago
```

→ You know the file is 12 days old. If the user asks "is this still up to date," you can hedge appropriately or offer to re-fetch from an authoritative source.

### `## Timestamps in your message`

The user pasted dated content (stock quotes, earnings reports, "from yesterday," "Q3 2025"). The hook parsed every timestamp and **pre-computed the gap to now**.

**Required discipline:** Do not redo the date arithmetic in your response. The hook has done it. Apply the result.

```
## Timestamps in your message
  "Q3 2025"        → ~6 months ago  (Q3 2025, announced ~mid-Nov 2025)
  "2026-05-24"     → 4 days ago
```

→ When discussing the Q3 2025 earnings, surface that "two earnings cycles have happened since" (because Q4 2025 and Q1 2026 are now in the past). Do not project forward as if Q3 2025 were the latest available.

### `## Time-sensitive phrasing detected`

The user's prompt contains language that suggests time-decay matters ("current price", "latest version", "is X still"). The signal is informational — apply your judgment.

**Required discipline:** If you would otherwise have answered from training data without hedging, *hedge*. Cite your cutoff. Recommend the user verify via a current source (WebFetch, official data, fresh tool call).

### `## Watchlist`

Files the user (or a config) marked for tracking. State changes since last turn are surfaced. Apply this when the change matters to the current question.

## The principle

**The hook does the computation. You apply the verdict.**

You are not reliable at multi-step date math. The hook is. When you see "147 days ago" in the AsOf block, that's a number computed precisely in Python — use it. Do not try to verify or recompute. The boundary cases (end-of-month, leap years, "147 days ago") fail unreliably in LLM arithmetic; they succeed reliably in Python. Trust the verdict.

## When to call `asof_query`

If you need a specific freshness check the hook didn't surface, you can call `asof_query` as a tool. It accepts:

- A file path: `asof_query("/path/to/file")` — returns mtime, age, and freshness verdict if there's a recorded Read
- A URL: `asof_query("https://...")` — returns "unknown" with a recommendation to WebFetch (V2: HEAD-request check)
- A timestamp string: `asof_query("2025-11-15")` or `asof_query("Q3 2025")` — returns the parsed date + gap
- A model ID: `asof_query("claude-opus-4-7")` — returns training cutoff + gap
- Any text: `asof_query("yesterday's report")` — extracts any temporal references and surfaces gaps

Use it when:
- The user references something time-sensitive that the auto-push didn't catch
- You're about to make a claim that hinges on a specific time relationship
- You want to confirm a gap before grounding analysis on a particular datum

## When the hook stays silent

Most casual exchanges produce no AsOf output. That's correct. The skill is *quiet by design*. Habituation defeats the catch — when you see an AsOf block, it's worth your attention. When you don't, the routine is routine.

## Honest limits

- The hook tracks files **you have touched this session**. Files outside your tool history aren't tracked unless mentioned in the prompt or on a watchlist.
- URL freshness is **opt-in tier** — V1 does not auto-HEAD-request URLs. If you need URL freshness, use WebFetch.
- The hook does not bridge across sessions. Cross-session continuity requires persistent state outside the skill.
- The hook cannot infer when content INSIDE a file was generated. A vacation-plan.md authored 60 days ago has a 60-day-old mtime; the prices INSIDE the file are 60 days old too, but only if the file was static since write. If the agent annotated the file with `[as-of: 2026-05-27]` markers when writing it, the analyst parser will catch them.

## Schema version

This skill expects AsOf hook output at schema version `0.1.0` or compatible (per the version contract in `asof_core/version.py`). If the hook emits a major-version-mismatched schema, the directive block will include an `INCOMPATIBLE` notice. Surface that to the user — the hook and prose are out of sync.
