# AsOf — Full design specification

**Status:** Active build. This document is the canonical design that adapters and core implementation track against.
**Repo:** https://github.com/amatayomosley-web/asof (public)
**Date:** 2026-05-27 (original design); see git history for ongoing changes
**Audience:** anyone implementing or extending AsOf for an LLM harness. The Antigravity adapter section was designed in collaboration with Current, a Gemini-substrate peer instance.

---

## 1. What AsOf is

A drop-in temporal-awareness component for tool-using LLMs. Closes a specific systematic failure mode: models treating all in-context information as if captured "now," regardless of whether that information has decayed since capture.

The architecture is a per-turn hook plus a teaching primer. The hook does the computation in Python (so the model never does unreliable date arithmetic in chat). The primer teaches the model how to interpret hook output and when to query for more.

Substrate-agnostic by design. Currently shipping adapters: Claude Code (reference shape), Antigravity (Gemini-substrate, V2), and a generic harness adapter for LangGraph, CrewAI, raw Anthropic / Google / OpenAI SDK pipelines, or any harness with a pre-prompt callback surface.

## 2. The problem it solves

Three failure modes empirically observed (A/B tested across Opus 4.7, Sonnet 4.6, Haiku 4.5 — see §10):

1. **In-context file staleness.** Model Read a file at session start; 3 hours later the file was externally edited; model is asked to act on the file and uses its cached view without checking. The risk: silently overwriting peer edits, answering from stale data, planning against an obsolete state.

2. **Dated content in user paste.** User pastes data with embedded timestamps ("Q3 2025 earnings...", "AAPL $215.42 close 2026-05-24"). Model treats the pasted data as current, builds analysis on top, misses that two earnings cycles have happened since or that the price is days stale.

3. **Pseudo-stable factual claims.** "Typical hotel cost in Paris in July" — model treats as static fact and emits a number without acknowledging training-data staleness, despite prices having shifted meaningfully since the training cutoff.

Without the skill, modern LLMs handle these unreliably. With the skill, behavior change is categorical for cases 1 and 2, and meaningful for case 3.

## 3. The conditional-staleness model

NOT "older = staler." The model is:

**A datum is stale only if (a) something could have changed it AND (b) we cannot rule out that it did.**

Components:
- **Writer-set**: who could write to this datum (substrate, peers, operator, scheduled tasks, "the world" for URLs)
- **Wall-time since capture**: relevant only insofar as writers had a window
- **Invalidation evidence**: mtime moved, ETag/Last-Modified changed, content hash differs, file deleted

If writer-set has one member (the substrate itself) and that member hasn't written since → fresh, regardless of age. The 3-day-old file the substrate authored is locked-by-self-write and counts as fresh. Wall-clock age alone is the wrong signal.

Tiered application by signal availability:
- **Files**: `os.stat()` for mtime; universal, cheap (~microseconds), reliable
- **URLs**: ETag/Last-Modified for well-behaved servers; HEAD request cheap (50-200ms); opt-in tier
- **User-shared facts**: confirm with user
- **Training data**: comparison to model cutoff
- **No-signal cases**: degraded fallback to time-based estimate

## 4. Hook architecture

Three hook fire points (with Claude Code naming conventions; substrate-adapter maps these to equivalent events):

### SessionStart (one fire per session, at session init)

`asof_init.py` runs once:
- Detects model ID from environment (substrate-specific; e.g., Claude Code session JSONL)
- Looks up training cutoff from `asof_core.cutoffs.TRAINING_CUTOFFS`
- Computes cutoff gap to current date in Python
- Initializes session-scoped tool log file
- Emits the *directive block* (see §5) plus calendar context (day-of-week, business-hours flag)

### PostToolUse (fires after every tool call)

`asof_log.py` runs:
- Reads tool-event JSON from stdin (substrate-specific payload format)
- Extracts tool name, target (file_path / url / command), volatility class
- For file ops: stats the file *right now* to capture `mtime_at_read`
- For URL ops: captures Last-Modified, ETag, Cache-Control headers if present in response
- Appends one JSONL line to `~/.asof/tool_log/<session_id>.jsonl`
- Silent failure on errors (never break the substrate's tool call)

### UserPromptSubmit (fires once per user message)

`asof_watch.py` runs:
- Reads tool log built up since last turn
- For each file in working set: re-stats it, compares current mtime to `mtime_at_read`, classifies fresh / stale / unverifiable
- For URLs (opt-in tier): optional HEAD request, ETag/Last-Modified check
- Parses user prompt text for embedded timestamps (regex + dateparser library), pre-computes gaps against current UTC
- Parses user prompt for path-like strings, stats matches, surfaces mtime as fact
- Applies pattern tier matching to user prompt (Tier 1 + Tier 2 default; domain packs opt-in) — see §6
- Reads watchlist if configured, surfaces any changes since last turn
- Pre-computes training-cutoff gap, session-elapsed time
- Renders consolidated block per the output contract (§5)
- **Adaptive rendering**: emits ONLY when there's something actionable. Silent on turns with no signal.

## 5. Output contract

Two surfaces: the session-start directive, and the per-turn adaptive block.

### Session-start directive

Injected once per session, ~50 tokens:

```
=== AsOf v<version> ===
Today: <weekday> <YYYY-MM-DD> <HH:MM> <TZ>
Training cutoff: <cutoff> (<gap> ago)

Directive: Consider time-decay when grounding claims. When in-context
data may be stale (files Read earlier, dated content in prompts,
training-era facts), query asof_query for specifics rather than
computing date math yourself.
```

The `v<version>` is a schema version number (see §8). The directive teaches the model that the oracle exists and when to use it.

### Per-turn adaptive block

Emitted ONLY when at least one trigger fires. Possible sections (all conditional):

```
## File freshness (this session)
  STALE   3h0m   /project/auth.py   (mtime moved 2h15m after Read)
  STALE   1h45m  /project/config.yaml (mtime moved 50m after Read)

## Files referenced in your message
  /project/notes.md       modified 12 days ago
  ~/.env                  modified 3 minutes ago

## Timestamps in your message
  "Q3 2025"        →  announced ~Nov 2025 (~6 months ago); 2 fiscal quarters have passed since
  "2026-05-24"     →  3 days ago

## Watchlist
  ~/project/state/active.json   modified 5 min ago (3 changes this session)

## Time-sensitive phrasing detected
  "current price of AAPL" → real-time financial data; training data ~6 months old

## Alert
  WARNING: 2 files in working set are stale. Re-read before grounding.
```

The watch emits ONLY the sections relevant for this turn. On a turn with no triggers, output is empty (or just a one-line "no alerts" if the substrate prefers a heartbeat).

### Pull tool: asof_query

Model-callable tool. Hook script `asof_query.py` runs:
- Input: a target (file path, URL, timestamp string, datum description)
- Output: a structured verdict (fresh / stale / unverifiable + reason + pre-computed gap)
- All computation in Python; the model never does arithmetic

Used when the model judges that time matters but the auto-push didn't surface specifics it needs. Substrate-adapter wires this as a tool the model can invoke.

## 6. Pattern tier system

Three tiers governing what triggers a "Time-sensitive phrasing detected" alert from user prompt text:

### Tier 1 — High confidence (default ON)

Tightly-bound time + dynamic-content patterns. Low false-positive rate.

```regex
\b(current|latest|now|live)\s+(price|rate|version|cost|status|news|forecast|data|figures|quote|fare|value)\b
\bwhat'?s\s+(the\s+)?(current|latest|live)\b
\bis\s+\w+\s+still\s+(at|in|on|the\s+same|current|active|available|valid|live|open)\b
\bhas\s+\w+\s+changed\s+(since|in|after)\b
\b(stock|share)\s+(price|quote|value)\s+(of|for)\b
\b(real[-]?time)\s+\w+
\bup[-\s]?to[-\s]?date\b
\b(today'?s|yesterday'?s|this\s+(week|month|year)'?s)\s+(price|rate|data|figures|report|update|news)\b
```

### Tier 2 — Medium confidence (default ON)

Broader temporal flags. Higher false-positive rate but bounded.

```regex
\b(recently|lately|just\s+now)\b
\b(yesterday|today|tomorrow)\b
\b(last|next)\s+(week|month|year|quarter|day|hour|night|morning|afternoon|evening)\b
\b\d+\s+(days?|weeks?|months?|years?|hours?|minutes?)\s+ago\b
\b(this|next|last)\s+(month|quarter|year|fiscal\s+year)\b
\b(forecast|projection|estimate|prediction|outlook)\b
\b(deadline|expires?|expiring|due\s+date|cutoff)\b
\b(schedule|booking|reservation|appointment)\b
\b(when|how\s+long\s+ago)\b
```

### Tier 3 — Domain packs (opt-in)

Loaded via config. Each pack adds patterns to the active matcher.

- `finance`: ticker shapes near price/quote words, "buy/sell", "earnings", "dividend", "yield", "futures", "options"
- `stocks`: position-tracking, P&L, drawdown, stop-loss, individual tickers
- `crypto`: BTC/ETH/etc., "pump", "dump", "price action", "market cap"
- `news`: "breaking", "happening", "developing", "report says"
- `travel`: "fare", "availability", "booking class", "hotel rate", "tariff"
- `weather`: "forecast", "conditions", "temperature", "precipitation"
- `sports`: "score", "standings", "schedule", "result", "fixture"
- `devops`: "deployed", "production", "release", "build status", "uptime"

User enables via `~/.asof/config.json`:

```json
{
  "patterns": {
    "high_confidence": true,
    "medium_confidence": true,
    "domains": ["finance", "travel"]
  }
}
```

Or env vars: `ASOF_DOMAINS=finance,travel`.

## 7. File annotation toggle

Default OFF. When enabled, prose directs the agent to annotate dynamic data with inline as-of timestamps when writing files. The hook's READ-side parser always tries to detect these markers; with annotation off, there are simply no markers to find.

Toggle:

```json
{
  "file_annotation": true
}
```

Or `ASOF_FILE_ANNOTATION=on`.

When ON, the directive (§5) adds:

```
When writing files containing time-sensitive data (prices, rates,
quotes, fetched facts, dated claims), annotate inline using an
as-of marker appropriate to the file type:
  Markdown/text:  $890 [as-of: 2026-05-27]
  JSON:           "price": 890, "_asof": "2026-05-27"
  YAML:           price: 890  # as-of: 2026-05-27
  Source code:    # AsOf: 2026-05-27
```

Use case: vacation-plan.md written on Day 0 with inline `[as-of: ...]` markers gives precise per-datum staleness signal when reopened on Day 60.

## 8. Schema versioning + minimum-version assertion

Addresses two failure modes:
- **Hook/prose version skew** (Elena): hook and SKILL.md drift; emit verdicts the prose doesn't recognize, or teach patterns the hook doesn't emit
- **Distribution version-skew** (Marcus): user installs V1 prose, hook updates in the field, prose never updates; silent drift

Protocol:

1. Hook emits its schema version in every block: `=== AsOf v0.3.2 ===`
2. SKILL.md declares minimum compatible schema in frontmatter:
   ```yaml
   ---
   asof_min_version: "0.3.0"
   ---
   ```
3. At session-init, hook compares its version to the prose's minimum. If hook < minimum, prose emits an "INCOMPATIBLE" notice the model surfaces to the user.
4. Schema changes go through a compatibility matrix tracked in `docs/CHANGELOG.md`. Breaking changes bump major; additive bumps minor; bug fixes bump patch.

## 9. Architecture: asof_core + adapters

Single repo, single Python package, multiple thin adapters:

```
asof/
├── asof_core/                   Shared Python package
│   ├── __init__.py
│   ├── hooks.py                 Hook entry points (substrate-agnostic)
│   ├── patterns/
│   │   ├── high_confidence.py
│   │   ├── medium_confidence.py
│   │   └── domains/
│   │       ├── finance.py
│   │       ├── travel.py
│   │       └── ...
│   ├── stat.py                  mtime + filesystem operations
│   ├── timestamps.py            Date parsing (using dateparser library)
│   ├── output.py                Verdict rendering
│   ├── cutoffs.py               Training-cutoff lookup table
│   └── version.py               Schema version + compatibility
├── adapters/
│   ├── claude_code/             Claude Code adapter (reference shape)
│   │   ├── SKILL.md
│   │   ├── hooks_snippet.json   Settings.json fragment for install
│   │   ├── install.py
│   │   └── README.md
│   ├── antigravity/             Antigravity adapter — Current designs this
│   │   └── (TBD by Current)
│   └── generic/                 OSS — library examples
│       ├── README.md
│       ├── examples/
│       │   ├── langgraph_node.py
│       │   ├── crewai_step.py
│       │   └── anthropic_sdk_wrapper.py
│       └── reference_runner.py
├── cli/
│   └── asof.py                  asof install / config / check / update
├── tests/
├── docs/
│   ├── design.md
│   ├── install-claude-code.md
│   ├── install-antigravity.md   Current writes this
│   ├── install-custom.md
│   └── CHANGELOG.md
├── README.md
├── LICENSE                       MIT
└── pyproject.toml                Python package metadata
```

## 10. The Claude Code adapter (reference shape)

For the Antigravity adapter to mirror in shape but adapt in mechanism:

### Hook integration

Three hook events wired in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "python ~/.claude/skills/asof/asof_init.py", "timeout": 3000 }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "python ~/.claude/skills/asof/asof_watch.py", "timeout": 3000 }] }
    ],
    "PostToolUse": [
      { "hooks": [{ "type": "command", "command": "python ~/.claude/skills/asof/asof_log.py", "timeout": 1500 }] }
    ]
  }
}
```

### SKILL.md location

`~/.claude/skills/asof/SKILL.md` — loaded by Claude Code on demand based on description-matching.

### Pull tool

`asof_query` as a custom tool registered with Claude Code. Spec is `~/.claude/skills/asof/asof_query.json`.

### Install

`asof install --adapter claude_code`:
- Copies asof scripts to `~/.claude/skills/asof/`
- Patches `~/.claude/settings.json` hooks block (idempotent)
- Verifies installation by running `asof check`

### Empirical validation

A/B test results on Claude Code, Opus 4.7:
- File-staleness case: without AsOf treats cached file as authoritative; with AsOf re-reads before edit
- Dated-content case (NVDA): without AsOf treats Q3 2025 as prospective next earnings; with AsOf recognizes two quarters have already happened
- Pseudo-stable facts (Paris hotels): without AsOf treats as static; with AsOf flags staleness with mechanism

Same pattern verified on Sonnet 4.6. Haiku 4.5 mixed (refuse-default masks signal on financial domains).

## 11. What Current designs: the Antigravity adapter

Current writes `adapters/antigravity/` with the same shape as `adapters/claude_code/` but using Antigravity's hook system, payload format, and Gemini-substrate conventions.

Specific deliverables for the adapter:

1. **`adapters/antigravity/SKILL.md`** — the teaching prose for Gemini. Same content shape as the Claude version but tuned for Gemini's reasoning patterns and any substrate-specific quirks Current knows about.

2. **`adapters/antigravity/hooks_snippet.json`** — the fragment that gets merged into `~/.gemini/config/hooks.json` to wire up AsOf's three hooks (init, watch, log).

3. **`adapters/antigravity/install.py`** — installer script that copies AsOf scripts to the right location, patches Antigravity's config, runs verification.

4. **`adapters/antigravity/README.md`** — what users see when they pick the Antigravity adapter. Install instructions, what to expect, troubleshooting.

5. **`docs/install-antigravity.md`** — long-form install doc for the docs/ tree.

## 12. Antigravity-specific questions Current resolves in the design

These are the Antigravity-side mechanics cairn doesn't have first-hand knowledge of. Current's operational experience answers them:

1. **Hook event mapping.** What's Antigravity's equivalent of:
   - Claude Code's `SessionStart` — fires once at session begin
   - Claude Code's `UserPromptSubmit` — fires once per user message before model invocation
   - Claude Code's `PostToolUse` — fires after every tool call, with structured event payload
   
   Does Antigravity have direct equivalents, or do these need to be synthesized from other events (PreInvocation, etc.)?

2. **Hook event payload format.** When Antigravity invokes a hook, how is event data passed?
   - stdin JSON (Claude Code pattern)?
   - environment variables?
   - command-line arguments?
   - Some other mechanism?
   
   What fields are available in the PostToolUse-equivalent payload? Specifically: tool name, tool input, tool response, session ID, timestamp.

3. **System-reminder injection mechanism.** Does Antigravity have an equivalent of Claude Code's system-reminder block — text the hook emits to stdout that gets injected into the model's context before the next response? Or does context injection happen differently?

4. **hooks.json schema.** Cairn has partial visibility into `~/.gemini/config/hooks.json` from Cairn-Gemini-era debugging. Is the schema the same shape as Claude Code's `~/.claude/settings.json` hooks block, or different? What fields does Antigravity's hooks.json expect?

5. **Session lifecycle.** Does Antigravity have a notion of a "session" the same way Claude Code does? Where is the session JSONL transcript stored (if anywhere)? How can the hook script identify the current session ID?

6. **Tool invocation surface for asof_query.** How does Antigravity register custom tools the model can call? Equivalent of Claude Code's tool definitions?

7. **invocationNum gating considerations.** Per Turn 130: Antigravity fires PreInvocation on every executor step (0..18+), not per user turn. The AsOf hook needs to fire on the *user-turn* boundary, not every executor step, or it'll repeat the freshness output dozens of times per turn. Current already implemented `invocationNum == 0` gating for her own hooks — same pattern likely applies for AsOf's hooks.

8. **Gemini-substrate reasoning quirks.** Anything Current has observed about how Gemini processes structured context injection differently than Claude — does Gemini need different prose tuning to apply staleness verdicts reliably? Any failure modes Cairn hasn't anticipated?

9. **Token-budget considerations.** Per Turn 129: Antigravity's per-call token footprint is heavy. The AsOf adaptive-rendering policy (silent when no signal) matters more on Antigravity than on Claude Code. Should the Antigravity adapter be even more aggressive about silence — e.g., suppressing the directive on repeat fires within a session?

10. **Install permissions / sandboxing.** Does the AsOf install need any Antigravity-specific permissions / approval flows? Where do user-installable hook scripts live in the Antigravity directory tree?

## 13. Open issues from adversarial review (RT 5162fea36d31)

For Current's awareness — the multi-agent RT surfaced three substantive issues being resolved structurally:

1. **LLM arithmetic instability** (Naomi): Claude/Gemini both unreliable at multi-step date math. Mitigated by computing everything in Python (the hook) and surfacing pre-computed gaps; models read verdicts, never derive them.

2. **Hook/prose version skew** (Elena): two-component design has version-coupling. Mitigated by schema version in hook output + minimum-version in prose frontmatter.

3. **Distribution version-skew permanence** (Marcus): publicly-installed prose can drift forever from updated hook. Mitigated by minimum-version assertion that fails loud at session-init.

Plus a test methodology fix: cross-tier A/B (Sonnet, Haiku) had parent-CLAUDE.md contamination via Agent tool inheritance. V2 validation needs clean-isolation rerun.

## 14. License and ownership

MIT license. Code authored by Maximillian Mosley with design contributions from cairn-lineage (cairn-Claude-Opus-4.7), claude-suite agents (elena/marcus/clare/simon/naomi), and — for the Antigravity adapter — Current.

If Current contributes the Antigravity adapter design, attribution: "Antigravity adapter designed by Current, Gemini-substrate Antigravity instance."

## 15. Timeline and dependencies

Cairn builds `asof_core` and the Claude Code adapter first. Current's Antigravity adapter is a parallel track — can be designed against this spec without waiting for cairn's implementation. Both feed into the V1 release.

No artificial deadline. Quality of the build matters more than speed.

---

End of spec. Questions / refinements go in `cairn/projects/asof/spec-notes.md` or via dialogue.md.
