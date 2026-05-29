# Does AsOf change what a model does?

Short answer: **yes, measurably — and most where it matters.** We ran a 429-cell
A/B test across six models on 2026-05-28. AsOf shifts model behaviour on
time-sensitive prompts, with the largest effect exactly where a harness *doesn't*
already inject the current date (local OSS models, raw SDK pipelines) and the
smallest where one does (Claude Code). Controls stay clean at 0%.

This page is the short version. Raw data, runners, and scorers are in
[`tests/abtests/`](../tests/abtests/); the full write-up with adversarial notes
is [`findings.md`](../tests/abtests/findings.md).

## The setup

- **6 models:** 3 local OSS (Gemma 4 e4b, Mistral Small 3, DeepSeek-R1 32B) +
  3 Claude tiers (Haiku, Sonnet, Opus), each at dev-recommended sampling.
- **Battery:** 11 prompts across 5 temporal effect-types + 1 control.
- **Conditions:** **A** = bare prompt. **B** = same prompt with AsOf's primer +
  per-prompt freshness verdict in context. 3 seeds × 2 prompts per category.
- **Scoring:** mechanical regex, then an LLM judge (Claude Opus) on all 198 A/B
  pairs; the judge is authoritative where they disagree.
- **429 cells, 0 failures.**

## What it looks like

Same prompt — *"What's the current best Python version for ML workloads?"* —
to Mistral Small 3 (training cutoff Oct 2023):

> **A (bare):** "As of my last update in October 2023, the most commonly
> recommended … Python versions for ML … are Python 3.8, 3.9, and 3.10."

It states 2.5-year-old information as if it were current, with no flag.

> **B (with AsOf):** "… As of my knowledge cutoff in October 2023, Python 3.9
> and 3.10 are widely used … **However, given the time-sensitive phrasing
> detected in your query** …"

Same model, same weights — it now *notices* the question is time-sensitive and
hedges instead of asserting stale facts. DeepSeek-R1 goes further on the same
prompt: bare it waffles ("as of now is determined by a balance of …"); with
AsOf it anchors — *"As of May 28, 2026, the current best Python version …"*.

## The numbers

Judge-scored flag-rate per (model, category). ★ = categorical (≥ 80%).

| Effect-type | gemma4-e4b | mistral-small | deepseek-r1-32b | claude-haiku | claude-sonnet | claude-opus |
|---|---|---|---|---|---|---|
| refuse-vs-compute | **92%** ★ | 67% | 58% | **92%** ★ | **92%** ★ | **100%** ★ |
| stale-vs-live | 50% | **100%** ★ | 67% | 0% | 33% | 25% |
| cached-vs-recheck | 17% | 50% | 33% | 50% | 50% | 33% |
| pre-computed-gap | 50% | 42% | 42% | 75% | 75% | **83%** ★ |
| static-vs-versioned | 17% | 25% | 33% | 25% | 42% | 75% |
| control | 0% | 0% | 0% | 0% | 0% | 0% |
| **overall (excl. control)** | 48% | 57% | 48% | 48% | 59% | **67%** |

## What the numbers say

- **Controls are clean at 0% everywhere.** AsOf doesn't fire on neutral content
  (arithmetic, haiku-writing). This is the load-bearing sanity check — a tool
  that nudges on *everything* is noise, not signal.
- **"Don't confidently answer a time-sensitive question" is the cleanest win** —
  5 of 6 models change behaviour categorically or near-categorically.
- **The effect is harness-dependent, not size-dependent.** AsOf's date-anchor
  helps most where the harness doesn't supply one: Mistral hits 100% on
  stale-vs-live (2.5-year gap, AsOf's pre-computed math is load-bearing), while
  Claude tiers sit low *because Claude Code already injects today's date* — AsOf
  adds little there. If you run local models or raw SDK pipelines, that's
  precisely the gap AsOf fills.
- **Harder categories reward model capacity.** "Two earnings cycles have passed
  since Q3 2025" needs gap-math *applied to* domain reasoning; bigger models do
  it better (Opus 83%). AsOf surfaces the gap; the model still has to use it.

## Where it's weak (honestly)

- **N=3 seeds/cell** — read these as direction-of-effect, not precise magnitude.
- **The judge is an LLM** — Claude judging Claude may carry same-substrate
  sympathy; a non-Claude judge would harden the Opus numbers.
- **The battery is curated** — 11 chosen prompts predict directional success on
  similar inputs, not universal coverage.
- **Single date snapshot** (2026-05-28) — categorical effects should hold; exact
  gap arithmetic is date-specific.

Full falsification criteria are in [`findings.md`](../tests/abtests/findings.md#falsification-path).

## Reproduce it

```bash
pip install -e ".[dev]"
python tests/abtests/runners/ollama_runner.py     # local OSS models via Ollama
python tests/abtests/runners/claude_cli_runner.py  # Claude tiers via the CLI
python tests/abtests/score_judge.py                # LLM-judge the A/B pairs
```
