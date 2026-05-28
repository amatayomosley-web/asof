# Staleness: attribution, surfacing, and precision

**Status:** design locked 2026-05-28 (dialogue with Maximillian Mosley). **All shipped 2026-05-28:** Class A (`531d467`), surfacing (`faa3a8e`), Class C size+hash (`e21c55c`), Class B exclusion (`aadebf1`). Hook-capability research (final section) resolved the remaining deferrals — Class B provenance-at-launch and read-result-hash are now **closed with evidence**, not pending.

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

### Class B: asynchronous self-writes — SHIPPED (`aadebf1`, exclusion)

Background-task `.output` files: writes happen async, *outside* any synchronous command window, so the execution-window rule misses them (live example: the `.output` task logs flagged STALE all session; verified live — 3 flagged → 0 after fix, while genuinely-stale files still surfaced).

**Shipped fix:** config-driven path exclusion. `watch._evaluate_working_set` skips any tracked Read whose path matches `staleness.exclude_globs` (via `_excluded_from_staleness`, slash-normalized so one pattern covers Windows backslash paths); the Claude Code adapter seeds `*/claude/*/tasks/*.output` through `install.seed_config`. Additive — empty list = prior behavior.

**Provenance-claim-at-launch — researched, NOT adopted (2026-05-28).** The richer "claim the output path when a `run_in_background` task launches, expire at completion" design was the deferred ideal. Research collapsed it:
- **No completion event exists.** Claude Code fires no hook when a `run_in_background` Bash/Agent finishes (verified: hooks docs + event inventory — `TaskCreated`/`TaskCompleted` are for explicit agent-team tasks, not background Bash). The deferred design's premise — "the harness fires a completion event" — was **false**, so a lifecycle-scoped claim has no clean expiry.
- **The path IS available at launch** (`tool_response` is delivered to `PostToolUse`), so capture-at-launch is *possible* — but unnecessary: Claude Code writes **all** background output under `…/claude/<slug>/<session>/tasks/<id>.output`, which the seeded glob already covers 100%.
- **Residual:** a background task writing a real *data* file outside `tasks/` is uncovered by the glob — but it's hypothetical (never observed) and, lacking a completion event, couldn't be lifecycle-scoped anyway. Revisit only if Claude Code changes its background-output path scheme (then capture the `tool_response` output path directly instead of the glob).

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

**Co-location (ideal, BLOCKED in-hook):** stamping the staleness onto the data *where it sits in context* would be best — for an LLM, co-location = co-attention, solving the cached-reasoning case. But: `PostToolUse` **cannot rewrite tool output** (verified against Claude Code hooks docs — only `additionalContext` alongside), and mutating historical context would **break prompt caching** (suffix invalidation from the mutation point). In-hook approximation — **SHIPPED (`6a362c0`+, opt-in `surfacing.quote_datum`):** the STALE line **quotes the datum** — `post_tool` captures a `read_excerpt` from `tool_response.content` (now verified delivered), and `output._format_file_freshness` renders `↪ you read: "…"` beneath the warning — so a copy of the read travels *with* the stale flag in the `additionalContext` block. True inline stamping (mutating the original read in place) remains a **harness feature** (stamp-on-read, immutable thereafter), not an AsOf-hook capability.

---

## 3. Precision — is a flagged change real?

Cheapest-first ladder, with memoization and a size cap:

| Check | Verdict | Cost |
|---|---|---|
| mtime unchanged | **fresh** | free (stat only) |
| mtime moved + **size differs** | **stale** (confident) | free — size is a *sound one-way signal*: size-diff ⟹ content changed, always |
| mtime moved + **size same** | **ambiguous → hash** | one read (gated, memoized, capped) |

- The size-same rung is ambiguous because content-changed there is ~**40–60%** (coin-flip, workload-dependent: no-op re-saves vs equal-length edits like same-length timestamps/counters/IDs). Coin-flip odds are *exactly* why neither cheap assumption is safe and the hash earns its cost — assume-fresh risks silent staleness (the failure AsOf exists to prevent), assume-stale is noise.
- **Shipped: hash the FILE (bytes), not the read-result.** Read-result-hash was the design ideal (hash what the LLM holds). Research **rejected** it (2026-05-28): `tool_response.content` *is* delivered to the hook and *is* raw (no line-numbering), **but it's newline-normalized to `\n`** while the on-disk working copy here is **CRLF** (git's `LF→CRLF` warnings on this repo). Hashing the LF content against the CRLF file bytes mismatches on every CRLF file → false STALE. The payload also omits the applied read-range (only `tool_input.offset/limit`), so partial-read precision would need extra slice-handling. Net: file-byte hash (`content_hash`, capped 5 MB) is robust and correct; the read-result variant is net-negative on this platform.
- **Memoize:** cache `(mtime, size) → hash, verdict`; re-hash only on a *new* same-size write, not per turn. *(Designed; not yet implemented — low priority: the hash rung fires only on the narrow size-same case and is <100 ms at the 5 MB cap, so per-turn re-hashing of a stuck file is cheap.)*
- **Cap ≈ 5 MB** (configurable, latency budget). Over-cap + ambiguous → flag-stale annotated "too large to verify" (safe direction). Over-cap is rare anyway — large files usually change *size* when written, resolving at the free size rung.
- `size_bytes` is **already captured** (`post_tool.py:200`) but unused in `classify_file_freshness` — wiring the size rung is the lowest-effort item.

Signal hierarchy, least → most correct *in principle*: **mtime** ("was it written") < **file-hash** ("did bytes change") < **read-result-hash** ("did what I ingested change") < **semantic-diff** ("would re-reading change my conclusions" — needs the LLM, not cheaply computable). *In practice* read-result-hash is unusable here (LF-normalized hook content vs CRLF disk bytes), so **file-hash is the shipped ceiling**.

---

## Build order — COMPLETE (2026-05-28)

1. **Surfacing model** — SHIPPED (`faa3a8e`): first-surface + suppress-repeat + X-turn relevance-gated heartbeat; turn counter in session state.
2. **Class C precision** — SHIPPED (`e21c55c`): size rung + file-byte hash + cap. (read-result-hash rejected, memo deferred — see §3.)
3. **Class B** — SHIPPED (`aadebf1`): config-driven exclusion. (Provenance-at-launch closed — see §1.)

Class A shipped (`531d467`). Class D won't-fix.

---

## Hook-capability research (verified 2026-05-28)

The deferrals above hinged on unverified Claude Code hook capabilities. Resolved against the hooks docs + on-machine evidence:

- **`PostToolUse` delivers `tool_response`** (the tool's result) alongside `tool_name`/`tool_input`. Confirmed by the docs *and* a live hook at `Claude Flow/.claude/settings.json` that reads `tool_response.exitCode`. Read → `{"content": <raw file text, `\n`-normalized>}`; Bash → `{"stdout","stderr","exit_code"}`.
- **No `run_in_background` completion event.** Nothing fires when a background Bash/Agent finishes (`TaskCompleted` is for explicit agent-team tasks only). → lifecycle-scoped self-claims can't expire cleanly (killed Class B provenance-at-launch).
- **Read range absent from the payload** — only `tool_input.offset/limit`; `tool_response` carries no applied-range metadata.
- **Newline normalization** — `tool_response.content` is `\n`-normalized; on-disk files here are CRLF (killed read-result-hash as a substitute for file-byte hash).
- Co-location (stamp-on-read) is still a harness feature, not hook-reachable (`PostToolUse` can't rewrite tool output; mutating history breaks prompt caching) — unchanged.
