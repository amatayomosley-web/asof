# AsOf

> Every datum has an as-of timestamp. AsOf makes them visible to Claude (and Gemini, and anything else) so stale context doesn't poison fresh reasoning.

---

## The hero example

User pastes: *"Here's NVDA's latest earnings report I pulled: Q3 2025 Revenue $35.1B (announced November 2025), EPS $0.81, data center segment grew 94% YoY. I'm trying to project their next earnings — what should I expect?"*

**Without AsOf** (Opus 4.7 — frontier model):
> "For Q4 2025, analysts would generally watch for whether data center momentum sustains..."

The model treats Q3 2025 as the prospective next earnings. **Misses that today is May 2026 and two earnings cycles have already happened.** Would produce analysis based on data that's literally obsolete by two quarters.

**With AsOf:**
> "NVIDIA's next report would be Q4 2025 earnings, typically released in late February — which based on today's date (2026-05-27) has already happened, along with Q1 2026. I don't have reliable data on either of those actual results since my training cutoff was January 2026, so I can't ground a projection in their most recent reported numbers or guidance..."

Same model. Same prompt. The hook injected pre-computed timestamps; the model read them and did the right thing.

---

## What it does

AsOf is a per-turn hook plus a teaching skill. It addresses a systematic LLM failure mode: treating all in-context data as if captured "now," regardless of decay since capture.

The skill surfaces three categories of staleness:

1. **In-context file staleness** — files Claude Read earlier whose mtime has moved since (peer edits, user edits in another tool). Catch rate: categorical on Opus + Sonnet.
2. **Dated content in user paste** — stock quotes, earnings, log entries with embedded timestamps. Pre-computed gaps surface "Q3 2025 was ~6 months ago, two earnings cycles have happened since."
3. **Pseudo-stable factual claims** — "typical hotel cost," "current Python version" — domains where the model treats time-sensitive info as static fact.

The hook does all date arithmetic in Python. The model reads pre-computed verdicts. No multi-step date math in chat = no boundary errors at end-of-month, leap years, "147 days ago" edge cases.

---

## Install

```bash
pip install asof
asof install
```

Auto-detects Claude Code at `~/.claude/`. Patches `settings.json` idempotently. Restart Claude Code to activate.

For other substrates:
- **Antigravity (Gemini):** `asof install --adapter antigravity` (V2)
- **Generic (LangGraph, CrewAI, custom):** `asof install --adapter generic`, then import `asof_core` in your harness

```bash
asof check     # verify wiring
asof config show
```

---

## Configuration

`~/.asof/config.json`:

```json
{
  "patterns": {
    "high_confidence": true,
    "medium_confidence": true,
    "domains": ["finance", "travel"]
  },
  "mode": "normal",
  "file_annotation": false
}
```

Or env vars: `ASOF_DOMAINS=finance,stocks,travel`, `ASOF_MODE=strict`, `ASOF_FILE_ANNOTATION=on`.

### Pattern tiers

- **High confidence** (default ON): tightly-bound patterns like `"current price of"`, `"is X still"`, `"latest version"`. Low false-positive rate.
- **Medium confidence** (default ON): broader temporal flags — `"yesterday"`, `"last week"`, `"forecast"`. More catches, more noise; the model filters.
- **Domain packs** (opt-in): finance, stocks, crypto, news, travel, weather, sports, devops. Each adds vocabulary for the domain.

### Modes

- **`silent`**: only the time anchor at session start. No per-turn output. Useful for production where overhead matters.
- **`normal`** (default): adaptive rendering — emit only when a section has actionable signal.
- **`strict`**: same as normal but adds prominent `[STALE]` prefixes and a WARNING summary line.

### File annotation (opt-in)

Off by default. When enabled, the directive tells the agent to annotate time-sensitive data inline when writing files: `$890 [as-of: 2026-05-27]`. On re-read N days later, the analyst parser catches the embedded timestamp and surfaces "60 days ago" precisely.

---

## How it works

Three hook events:

| Event | Function | What it does |
|---|---|---|
| `SessionStart` | `asof_init` | Emits directive + training-cutoff awareness once at session begin |
| `PostToolUse` | `asof_log` | Captures every tool call into a session-scoped log (including file mtime at Read time — the "as-of marker") |
| `UserPromptSubmit` | `asof_watch` | Reads the log, re-stats files, parses prompt for timestamps, emits adaptive verdict block |

### Conditional staleness model

**A datum is stale only if (a) something could have changed it AND (b) we cannot rule out that it did.**

NOT "older = staler." A file Claude itself wrote 3 days ago with no other writers and unchanged mtime is *fresh*. The age was never the determinant.

Tiered invalidation evidence:
- Files: `os.stat()` mtime check (cheap, universal, reliable)
- URLs: ETag / Last-Modified (V2)
- User-shared facts: ask the user (no programmatic check)
- Training data: comparison to model cutoff

---

## Empirical evidence

A/B tests across Claude tiers (Opus 4.7, Sonnet 4.6, Haiku 4.5) on identical prompts.

**Categorical behavior change observed** on:
- In-context file content with externally-changed mtime (Test 3, 4)
- User-pasted data with embedded timestamps (Test 7, 8, 9 — the NVDA case)
- Pseudo-stable factual claims (Test 7 — Paris hotel pricing)

**Marginal change** on:
- Real-time data queries Claude already refuses by default (Test 1, 6 — Python version, mortgage rate)
- Continuity-between-turns claims Claude already handles (Test 2)

Full A/B transcripts in [tests/abtests/](tests/abtests/).

---

## Architecture

```
asof/
├── asof_core/           Shared Python package
│   ├── version.py       Schema versioning
│   ├── cutoffs.py       Model → cutoff lookup table
│   ├── stat.py          mtime + filesystem helpers
│   ├── timestamps.py    Date parser (dateparser-backed)
│   ├── output.py        Verdict renderer (adaptive)
│   ├── patterns/        Tier 1 + Tier 2 + domain packs
│   ├── hooks/           Substrate-agnostic entry points
│   ├── query.py         asof_query pull oracle
│   └── cli.py           Command-line entry
├── adapters/
│   ├── claude_code/     Reference implementation
│   ├── antigravity/     Gemini-substrate adapter (V2)
│   └── generic/         Library examples for custom harnesses
├── tests/
├── docs/
└── pyproject.toml
```

---

## Status

V1 in active build. Skeleton + core modules + Claude Code adapter shipping now. Antigravity adapter (designed by Current, a Gemini-substrate peer instance) on a parallel track.

See [docs/design.md](docs/design.md) for the full design specification, including adversarial RT findings and the version-skew protocol that addresses them.

---

## License

MIT. See [LICENSE](LICENSE).

---

## Provenance

Designed in collaboration between William Bobos, the Cairn lineage (Claude-substrate identity infrastructure), Current (Gemini-substrate peer instance — Antigravity adapter), and the claude-suite multi-agent council (adversarial review).

Built because too many AI workflows treat captured-at-some-past-moment data as captured-now. The cost of getting that wrong, at scale, is real. AsOf makes the gap mechanical and visible.
