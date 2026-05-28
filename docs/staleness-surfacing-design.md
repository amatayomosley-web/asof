# Staleness: attribution, surfacing, and precision

**Status:** design locked 2026-05-28 (dialogue with Maximillian Mosley). Class A **shipped** (`531d467`); surfacing model, Class B, and Class C **designed, deferred for build**.

## Problem this addresses

AsOf v0.1.0 **broadcasts**: every `UserPromptSubmit`, `watch._evaluate_working_set` re-lists every stale Read-file, forever. An 8-hour live session exposed two failure modes:
1. **False positives** — files edited via Bash (`sed -i`, `>`, `cp`, `git checkout`, formatters) were flagged STALE because only Write/Edit/MultiEdit counted as self-writes.
2. **Permanent repetition** — a true-but-irrelevant warning re-injected every turn for hours → habituation, the warnings become wallpaper.

Three orthogonal questions fell out, each with its own answer:

---

## 1. Attribution — which changes are "self"?

### Class A: synchronous agent writes — SHIPPED (`531d467`)

**Execution-window invariant:** a file whose mtime aligns (≤ `CONCURRENT_WRITE_TOLERANCE`, 5s) with the *completion* of the agent's own external-volatility command was written by that command. The `PostToolUse` hook fires at completion, so a just-written file has mtime ≈ now. `post_tool` re-stats tracked Read files and records a `_asof_self_write`; `watch._build_self_writes_index` honors it.

Generalizes across `sed`/`cp`/`mv`/`git`/build/formatters/MCP/PowerShell **without shell parsing** — the timestamp alignment is the evidence, not the verb. Fail-safe: a long command that writes >5s before completing leaves the file conservatively flagged (safe over-warn, never false silence). Verified live + unit-tested (2 new, 15/15 suite).

### Class B: asynchronous self-writes — DEFERRED

Background-task `.output` files: writes happen async, *outside* any synchronous command window, so the execution-window rule misses them (live example: the `.output` task logs flagged STALE all session).

**Fix:** provenance-claim at task launch — "I spawned the writer, so I own its output." Append a self-claim covering the output path when a `run_in_background` task is launched.
- **Lifecycle-scoped:** the claim expires at task completion (the harness fires a completion event). After completion, new edits to that path are external again — a permanent claim would wrongly swallow a later peer edit.
- **Verify first:** does the `PostToolUse` payload expose the background task's output path at launch? If not, derive it from the harness's task-dir convention — **adapter-specific, not a global constant** (exclusion lists rot; each adapter knows its own transient dirs).
- Better than the rejected "exclude harness temp dirs" idea: provenance also covers background tasks that write real *data* (a generated `report.md`), which a path-exclusion would wrongly ignore.

### Class D: cross-session same-identity writes — WON'T FIX

A cron/scheduled cairn session editing a file (e.g. `daily_review.md`) is genuinely external to *this* session. Leave it flagged — the conditional-staleness model says re-read. Self-identity ≠ this-session.

---

## 2. Surfacing — when to show a true staleness?

Rejected models and why:
- **Broadcast (current):** habituation; old entries dilute the one that matters.
- **Recency-decay-as-suppression:** wrong — staleness truth does *not* decay with time; hiding a still-true warning based on time trades correctness for quiet, and hides it right up until the rare moment you act on the stale copy.
- **Pure on-access:** worst for an LLM — it triggers on the re-Read action, but the LLM's *dominant* failure is reasoning from cached content with **no** re-Read (no trigger fires). It helps only the LLM that was already being careful.

**Locked model:**
- **First-surface: ALWAYS**, on detection. The true fact is delivered once, unconditionally.
- **Suppress every-turn repeats** — redundancy: re-stating a delivered, unchanged fact carries zero new information.
- **Re-surface every X turns** (default ≈ 12, configurable) *while still stale AND still in the working set* — a salience heartbeat against the LLM's recency-weighted attention (an old warning fades from salient context; the heartbeat restores it). X has a floor: too small re-introduces habituation.
- **Re-surface immediately** on re-access or a new change (new delta).
- **Stop** on resolution (re-read clears it) or when the file leaves the working set (first-surface already delivered; heartbeat goes quiet).

The distinction that keeps this coherent with the recency-rejection: **recency governs frequency (how often to restate), never truth (the fact is never hidden — first-surface always fires; the heartbeat continues only while relevant).**

**Co-location (ideal, BLOCKED in-hook):** stamping the staleness onto the data *where it sits in context* would be best — for an LLM, co-location = co-attention, solving the cached-reasoning case. But: `PostToolUse` **cannot rewrite tool output** (verified against Claude Code hooks docs — only `additionalContext` alongside), and mutating historical context would **break prompt caching** (suffix invalidation from the mutation point). In-hook approximation: the warning **quotes the stale datum**, so a copy of the data travels *with* the stale flag in the `additionalContext` block. True inline stamping is a **harness feature** (stamp-on-read, immutable thereafter), not an AsOf-hook capability.

---

## 3. Precision — is a flagged change real?

Cheapest-first ladder, with memoization and a size cap:

| Check | Verdict | Cost |
|---|---|---|
| mtime unchanged | **fresh** | free (stat only) |
| mtime moved + **size differs** | **stale** (confident) | free — size is a *sound one-way signal*: size-diff ⟹ content changed, always |
| mtime moved + **size same** | **ambiguous → hash** | one read (gated, memoized, capped) |

- The size-same rung is ambiguous because content-changed there is ~**40–60%** (coin-flip, workload-dependent: no-op re-saves vs equal-length edits like same-length timestamps/counters/IDs). Coin-flip odds are *exactly* why neither cheap assumption is safe and the hash earns its cost — assume-fresh risks silent staleness (the failure AsOf exists to prevent), assume-stale is noise.
- **Hash the READ RESULT, not the file** — it's the object the LLM actually holds (line-numbered, possibly partial via offset/limit). Fixes the partial-read false positive (file changes outside my read window) *and* makes the size cap rarely bite (a partial read of a huge file has a small result).
- **Memoize:** cache `(mtime, size) → hash, verdict`; re-hash only on a *new* same-size write, not per turn. Steady-state hash rate ≈ same-size write-events, **not** files × turns. A file written once then stable is hashed once, ever.
- **Cap ≈ 5 MB** (configurable, latency budget). Over-cap + ambiguous → flag-stale annotated "too large to verify" (safe direction). Over-cap is rare anyway — large files usually change *size* when written, resolving at the free size rung.
- `size_bytes` is **already captured** (`post_tool.py:200`) but unused in `classify_file_freshness` — wiring the size rung is the lowest-effort item.

Signal hierarchy, least → most correct: **mtime** ("was it written") < **file-hash** ("did bytes change") < **read-result-hash** ("did what I ingested change") < **semantic-diff** ("would re-reading change my conclusions" — needs the LLM, not cheaply computable).

---

## Build order

1. **Surfacing model** — first-surface + suppress-repeat + X-turn relevance-gated heartbeat. Fixes the structural per-turn noise; highest value. Needs a turn counter in session state.
2. **Class C precision** — size rung + read-result-hash with memo + cap. Kills no-op-write false positives.
3. **Class B provenance** — after verifying background-output-path capture.

Class A shipped. Class D won't-fix.
