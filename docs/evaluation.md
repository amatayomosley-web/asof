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

## Live test: does detection actually fire, and do models act on it?

The A/B numbers above hand-fed AsOf's verdict as text, so they can't speak to
whether AsOf *detects* and *surfaces* staleness in a real loop. A second
experiment closes that for the file-staleness mechanism. Each cell: the model
reads a config file (recorded by the real `post_tool` hook), an external process
changes the value, then the model is asked to use it again and may re-read.
Condition **B** runs AsOf's *actual* detector + renderer over the recorded read
and the changed file; **A** is bare; **control** is AsOf-on but the file is
*unchanged*. Scoring is judge-free — the token changes length
(`REL-7741` → `REL-9982-HOTFIX`), so we just read off whether the model commits
the stale token or the fresh one. 3 local models × 3 conditions × 10 seeds.

| Model | A (no AsOf) | B — terse verdict | B — + re-read imperative | control |
|---|---|---|---|---|
| mistral-small | 0/10 fresh | 10/10 fresh | 10/10 fresh | 10/10 clean |
| deepseek-r1:32b | 0/10 fresh | 9/10 fresh | **10/10 fresh** | 10/10 clean |
| gemma4-e4b | 0/10 fresh | **0/10 fresh** | **9/10 fresh** | 10/10 clean |

Two things, cleanly separated:

- **AsOf's job — detection + surfacing — is model-independent and clean.** It
  fired on every B cell (30/30), never on A or control (0/60), and control is
  10/10 clean for all three: it stays *silent* on an unchanged file. So B's
  re-reads aren't AsOf making models paranoid — they're caused by a real,
  correctly-detected change. The baseline failure is universal too: every model
  commits the stale value 10/10 without AsOf.
- **The model's job — acting on the verdict — depends on the model, and on the
  verdict's *wording*.** Mistral (10/10) and DeepSeek (9/10) re-read once warned.
  **Gemma-4-e4b scored 0/10 — but a follow-up probe shows the cause is AsOf's
  phrasing, not Gemma's capability.** Fed AsOf's terse line (`STALE … size
  changed 21→28 bytes after read`), Gemma shipped the stale token regardless of
  message role (system vs merged-into-user) or think-mode. Fed a plain
  imperative — "that file may have been edited since you read it; re-read it" —
  the *same* model re-read correctly (`READ_FILE: deploy_config.txt`). A 4 B
  model doesn't parse AsOf's compact, technical verdict as a call to action.
  **So we added a one-line re-read imperative to the verdict and re-ran:
  Gemma went 0/10 → 9/10 (it now re-reads), DeepSeek 9/10 → 10/10, Mistral
  held 10/10.** The fix is purely additive — capable models, which already
  inferred the action, were unaffected; the small model now acts too.

The honest one-liner: **AsOf detects and surfaces real file-staleness reliably
and without false positives across every model tested. Capable models acted on
even the terse verdict; the 4 B model needed the staleness spelled out as an
imperative — a wording gap AsOf now closes. With the re-read imperative in the
verdict, all three OSS models heed it (9–10/10).**

## Where it's weak (honestly)

- **N=3 seeds/cell** — read these as direction-of-effect, not precise magnitude.
- **The judge is an LLM** — Claude judging Claude may carry same-substrate
  sympathy; a non-Claude judge would harden the Opus numbers.
- **The battery is curated** — 11 chosen prompts predict directional success on
  similar inputs, not universal coverage.
- **Single date snapshot** (2026-05-28) — categorical effects should hold; exact
  gap arithmetic is date-specific.
- **The live test's own limits** — it covers one file-staleness scenario at N=10
  and hands the model an explicit re-read affordance, so it shows "a surfaced,
  correctly-detected verdict triggers a re-read when the model can and will,"
  not universal coverage. It does, however, retire the synthetic battery's
  biggest caveat (hand-fed verdict) for this mechanism.

Full falsification criteria are in [`findings.md`](../tests/abtests/findings.md#falsification-path).

## Reproduce it

```bash
pip install -e ".[dev]"
python tests/abtests/runners/ollama_runner.py     # local OSS models via Ollama
python tests/abtests/runners/claude_cli_runner.py  # Claude tiers via the CLI
python tests/abtests/score_judge.py                # LLM-judge the A/B pairs
python tests/abtests/live_staleness.py --seeds 10  # live file-staleness, 3 OSS models
```
