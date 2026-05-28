# AsOf

> Every datum has an as-of timestamp. AsOf makes them visible to LLMs so stale context doesn't poison fresh reasoning.

---

## The hero example

User pastes: *"Here's NVDA's latest earnings report I pulled: Q3 2025 Revenue $35.1B (announced November 2025), EPS $0.81, data center segment grew 94% YoY. I'm trying to project their next earnings. What should I expect?"*

**Without AsOf** (model under test in this example: Claude Opus 4.7; the failure mode replicates across every model tested — see Empirical Evidence below):
> "For Q4 2025, analysts would generally watch for whether data center momentum sustains..."

The model treats Q3 2025 as the prospective next earnings. **Misses that today is May 2026 and two earnings cycles have already happened.** Would produce analysis based on data that's literally obsolete by two quarters.

**With AsOf:**
> "NVIDIA's next report would be Q4 2025 earnings, typically released in late February. Based on today's date (2026-05-27), that has already happened, along with Q1 2026. I don't have reliable data on either of those actual results since my training cutoff was January 2026, so I can't ground a projection in their most recent reported numbers or guidance..."

Same model. Same prompt. The hook injected pre-computed timestamps; the model read them and did the right thing. The same behavior change holds across Gemma 4, Mistral Small 3, DeepSeek-R1, and all three Claude tiers.

---

## What it does

AsOf is a per-turn hook plus a primer that tells the LLM how to read its output. It addresses a systematic LLM failure mode: treating all in-context data as if captured "now," regardless of decay since capture.

The hook surfaces three categories of staleness:

1. **In-context file staleness.** Files the agent Read earlier whose mtime has moved since (peer edits, user edits in another tool). Catch rate: categorical across tested models — Claude Opus 4.7 and Sonnet 4.6, and the OSS models when running through a harness that exposes file-Read events.
2. **Dated content in user paste.** Stock quotes, earnings, log entries with embedded timestamps. Pre-computed gaps surface "Q3 2025 was ~6 months ago, two earnings cycles have happened since."
3. **Pseudo-stable factual claims.** "Typical hotel cost," "current Python version," and similar domains where the model treats time-sensitive info as static fact.

The hook does all date arithmetic in Python. The model reads pre-computed verdicts. No multi-step date math in chat means no boundary errors at end-of-month, leap years, or "147 days ago" edge cases.

---

## Install

```bash
pip install asoftime
asof install
```

Wires AsOf into your LLM harness via an adapter. Pass `--adapter <name>` explicitly, or let `asof install` auto-detect Claude Code if it sees `~/.claude/`. Currently supported adapters:

- **Generic harness** (LangGraph, CrewAI, custom Anthropic/OpenAI/Google SDK pipelines): `asof install --adapter generic`, then import `asof_core` from your hooks. Use this for any harness with a pre-prompt callback surface.
- **Antigravity (Gemini-substrate IDE):** `asof install --adapter antigravity` (V2)
- **Claude Code:** `asof install --adapter claude_code` (or just `asof install` — auto-detected). Patches `~/.claude/settings.json` idempotently; restart Claude Code to activate.

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
- **Medium confidence** (default ON): broader temporal flags including `"yesterday"`, `"last week"`, `"forecast"`. More catches, more noise; the model filters.
- **Domain packs** (opt-in): finance, stocks, crypto, news, travel, weather, sports, devops. Each adds vocabulary for the domain.

### Modes

- **`silent`**: only the time anchor at session start. No per-turn output. Useful for production where overhead matters.
- **`normal`** (default): adaptive rendering. Emits only when a section has actionable signal.
- **`strict`**: same as normal, plus prominent `[STALE]` prefixes and a WARNING summary line.

### File annotation (opt-in)

Off by default. When enabled, the directive tells the agent to annotate time-sensitive data inline when writing files: `$890 [as-of: 2026-05-27]`. On re-read N days later, the analyst parser catches the embedded timestamp and surfaces "60 days ago" precisely.

---

## How it works

Three hook events:

| Event | Function | What it does |
|---|---|---|
| `SessionStart` | `asof_init` | Emits directive and training-cutoff awareness once at session begin |
| `PostToolUse` | `asof_log` | Captures every tool call into a session-scoped log, including file mtime at Read time (the "as-of marker") |
| `UserPromptSubmit` | `asof_watch` | Reads the log, re-stats files, parses prompt for timestamps, emits adaptive verdict block |

### Conditional staleness model

**A datum is stale only if (a) something could have changed it AND (b) we cannot rule out that it did.**

Older does not mean staler. A file the agent itself wrote three days ago, with no other writers and unchanged mtime, is *fresh*. Age was never the determinant.

Tiered invalidation evidence:
- Files: `os.stat()` mtime check (cheap, universal, reliable)
- URLs: ETag and Last-Modified (V2)
- User-shared facts: ask the user (no programmatic check)
- Training data: comparison to model cutoff

---

## Empirical evidence

Tested 2026-05-28 across **six models × 11 prompts × 3 seeds = 429 cells**, scored both mechanically and by LLM-judge (Claude Opus, 198 A/B pairs). Models: three Claude tiers (Opus 4.7, Sonnet 4.6, Haiku 4.5) via `claude -p` in isolated subprocess + three local OSS models (Gemma 4 e4b, Mistral Small 3 24B, DeepSeek-R1 32B) via Ollama with vendor-recommended sampling.

Judge-scored weighted flag-rate per (model, category). Categorical = ≥ 80%.

| Effect-type | gemma4-e4b | mistral-small | deepseek-r1 | claude-haiku | claude-sonnet | claude-opus |
|---|---|---|---|---|---|---|
| refuse-vs-compute | **92%** | 67% | 58% | **92%** | **92%** | **100%** |
| stale-vs-live | 50% | **100%** | 67% | 0% | 33% | 25% |
| cached-vs-recheck | 17% | 50% | 33% | 50% | 50% | 33% |
| pre-computed-gap | 50% | 42% | 42% | 75% | 75% | **83%** |
| static-vs-versioned | 17% | 25% | 33% | 25% | 42% | 75% |
| **control** | **0%** | **0%** | **0%** | **0%** | **0%** | **0%** |

**Categorical wins**: refuse-vs-compute on 5/6 models (the cleanest AsOf effect); stale-vs-live on Mistral Small 3 (cutoff 2023-10 → today is 2.6-year gap, pre-computed math is load-bearing); pre-computed-gap on Opus.

**Architecture findings worth knowing**:
- **Stale-vs-live splits by harness, not model**: Claude tiers show 0-33% because Claude Code injects today's date natively. OSS models via Ollama show 50-100% because the harness doesn't.
- **Pre-computed-gap rewards capacity**: 75-83% on bigger Claude tiers, 42-50% on OSS — the model has to integrate gap math with domain reasoning.
- **Controls clean across all six models**: AsOf doesn't fire opportunistically on neutral content. Zero false-positive flag-rate on every model.

See [tests/abtests/findings.md](tests/abtests/findings.md) for the full writeup including adversarial notes, falsification paths, and the Gemma-thinking-mode token-budget caveat. Battery and runners are in [tests/abtests/](tests/abtests/).

---

## Architecture

```
asof/
├── asof_core/           Shared Python package
│   ├── version.py       Schema versioning
│   ├── cutoffs.py       Model to cutoff lookup table
│   ├── stat.py          mtime and filesystem helpers
│   ├── timestamps.py    Date parser (dateparser-backed)
│   ├── output.py        Verdict renderer (adaptive)
│   ├── patterns/        Tier 1, Tier 2, domain packs
│   ├── hooks/           Substrate-agnostic entry points
│   ├── query.py         asof_query pull oracle
│   └── cli.py           Command-line entry
├── adapters/
│   ├── claude_code/     Claude Code adapter (reference shape)
│   ├── antigravity/     Antigravity adapter (Gemini-substrate, V2)
│   └── generic/         Generic-harness library examples (LangGraph, CrewAI, raw SDKs)
├── tests/
├── docs/
└── pyproject.toml
```

---

## Status

V1 in active build. The substrate-agnostic `asof_core` package and three adapters are shipping: Claude Code (the reference shape), Antigravity (Gemini-substrate, designed in collaboration with Current — a Gemini-substrate peer instance), and a generic harness adapter for LangGraph / CrewAI / direct SDK pipelines.

See [docs/design.md](docs/design.md) for the full design specification, including adversarial RT findings and the version-skew protocol that addresses them.

---

## License

MIT. See [LICENSE](LICENSE).

---

## Provenance

Designed in collaboration between Maximillian Mosley, the Cairn lineage (Claude-substrate identity infrastructure), Current (Gemini-substrate peer instance, Antigravity adapter), and the Amatelier multi-agent council (adversarial review).

Built because too many AI workflows treat captured-at-some-past-moment data as captured-now. The cost of getting that wrong, at scale, is real.
</content>
</invoke>