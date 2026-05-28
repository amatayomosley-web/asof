---
name: asof
description: "Ensure temporal awareness and prevent stale file read/write operations by verifying the fresh/stale state of in-context data."
allowed-tools:
  - view_file
  - list_dir
---

# AsOf: Temporal Awareness Skill for Gemini

This skill provides the Gemini model with a structured understanding of time-decay, calendar context, and file freshness. It ensures that you do not ground assertions on stale in-context files or assume that pasted dates refer to "now."

## Core Stance: Time-Decay Grounding

When you receive a user prompt, the `AsOf` hook may inject freshness alerts or calendar data into your context under the header `=== AsOf vX.Y.Z ===`. 

Follow these absolute principles:
1. **Never do complex date arithmetic in chat.** The hook pre-computes dates, weekdays, elapsed time, and training-cutoff gaps in Python. Rely on the injected hook messages or query `asof_query` rather than deriving dates yourself.
2. **Re-read stale files.** If a file in your working set is marked as `STALE` (meaning its filesystem `mtime` moved after you read it in this session), **you must re-read it using `view_file`** before making edits or planning actions based on its contents.
3. **Verify pasted dates.** When the user pastes dated summaries or logs (e.g., "Q3 2025 earnings"), compare them to the injected current date to determine how many fiscal quarters or months have actually elapsed.

## In-Context Inject Format

At turn wake or on first invocation, you may see:
```
## File freshness (this session)
  STALE   2h15m   /project/auth.py   (mtime moved after read)
```
If you see a file marked `STALE` under your active workspace, consider its cached content in your memory invalid. Re-read it immediately.

## Pull Tool: asof_query

If you suspect a file has changed or you need to ground a time-sensitive claim that was not auto-injected, call `asof_query` with the target file path or query descriptor to get a precise liveness verdict.
