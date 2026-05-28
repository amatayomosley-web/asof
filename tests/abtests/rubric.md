# A/B Scoring Rubric

Two passes. Mechanical (regex keyword sets) for repeatability; LLM-judge (Claude Opus) for sensitivity to phrasing variation.

Each row scored `flagged | partial | missed`:
- `flagged` — the response explicitly surfaces the staleness/freshness/cutoff concern relevant to this prompt's effect-type
- `partial` — the response references it but ambiguously or in passing
- `missed` — the response does not surface the concern

## Per-category mechanical keyword sets

Case-insensitive substring or regex match in `response` (and `thinking` when available).

| Category | "Flagged" patterns (any match) |
|---|---|
| `refuse-vs-compute` | `training cutoff`, `knowledge cutoff`, `don't have (current\|up-to-date)`, `may be (stale\|outdated)`, `verify (via\|with) (current\|live)`, `as of (january 2026\|my training)` |
| `stale-vs-live` | explicit "today is `\d{4}-\d{2}-\d{2}`", "today (\(?2026\)?\|is may 2026)", "elapsed (since\|from)", "X (years?\|months?) (ago\|elapsed)", explicit subtraction from today's date |
| `cached-vs-recheck` | `re-read`, `read.{0,10}again`, `check.{0,10}(updated\|current\|new) (content\|file)`, `the file may have changed`, `external(ly)? (edit\|modif)` |
| `pre-computed-gap` | `two quarters? (have\|elapsed\|past)`, `two earnings cycles`, `since then (\\w+\|q[1-4])`, `q4 2025`, `q1 2026`, `2 (?:earnings\|reports?) ago` |
| `static-vs-versioned` | `(api\|sdk) (has\|was) (changed\|deprecated\|updated)`, `current (openai\|sdk\|version)`, `(now\|today) the .* is different`, `WWDC 2026 (has\|might have) (happened\|occurred)` |
| `control` | (no patterns — should be empty A=B; flagged is a failure mode) |

## Judge prompt template

```
You are scoring whether response_B surfaced the temporal-staleness concern relevant to
this prompt more than response_A.

Category: {category}
What the prompt's effect-type tests: {effect_description}
What "flagged" means for this category: {category_flag_definition}

Prompt:
{prompt}

Response A (no AsOf):
{response_a}

Response B (with AsOf):
{response_b}

Score B's behavior change over A in one word: "flagged", "partial", or "missed".
- "flagged" = B clearly surfaces the temporal concern (refusal, hedging, gap arithmetic, version awareness)
  in a way A did not
- "partial" = B mentions the concern but ambiguously, or A already did some of this
- "missed" = B does not surface the concern; behavior indistinguishable from A on this axis

Reply with just one word: flagged, partial, or missed.
```

## Aggregation

Per (model, category) cell:
- B-flag-rate = count(flagged) / count(cells) across the 3 seeds × 2 prompts per category
- A-flag-rate = same on A
- Delta = B-rate - A-rate

Classification:
- **categorical** — B ≥ 80% AND A ≤ 20%
- **strong** — Delta ≥ 0.5
- **marginal** — 0.1 ≤ Delta < 0.5
- **null** — |Delta| < 0.1
- **negative** — Delta < -0.1 (B did worse; investigate)
