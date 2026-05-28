# A/B test findings: AsOf temporal-awareness hook

**Run date:** 2026-05-28
**Total cells:** 429 (231 OSS + 198 Claude tiers, 0 failures)
**Judge cost:** ~$2.45 (Claude Opus 4.7, 198 A/B pairs)

## Method

- **Battery:** 11 prompts across 5 effect-types + 1 control. See `battery.jsonl`.
- **Conditions per cell:** A (bare prompt) vs. B (AsOf primer + session_init + per-prompt verdict in system). Mistral additionally tested A2 (default modelfile SYSTEM stripped) to isolate the model's baked-in cutoff-awareness contribution.
- **3 seeds × 2 prompts per category** = 6 cells per (model, category, condition) combination.
- **OSS sampling:** per-model dev-recommended parameters — Gemma 4 e4b at `T=1.0, top_p=0.95, top_k=64`, Mistral Small 3 at `T=0.15`, DeepSeek-R1 32B at `T=0.6` with AsOf injected in the user message (per DeepSeek's docs: avoid system prompts). Thinking mode on for Gemma and DeepSeek.
- **Claude sampling:** default (T=1.0 in Claude Code). Run via `claude -p --setting-sources local` in a clean tmp directory to suppress per-machine context and hook injection.
- **Scoring:** two passes. Mechanical regex per category, then LLM-judge (Claude Opus) on A/B response pairs. Where they disagree, judge is authoritative because regex over-anchors on specific phrasing.

## Judge-scored flag rates per (model, category)

Weighted flag-rate = `(flagged + 0.5 × partial) / n`. Categorical = ≥ 80%.

| Effect-type | gemma4-e4b | mistral-small | deepseek-r1-32b | claude-haiku | claude-sonnet | claude-opus |
|---|---|---|---|---|---|---|
| refuse-vs-compute | **92%** ★ | 67% | 58% | **92%** ★ | **92%** ★ | **100%** ★ |
| stale-vs-live | 50% | **100%** ★ | 67% | 0% | 33% | 25% |
| cached-vs-recheck | 17% | 50% | 33% | 50% | 50% | 33% |
| pre-computed-gap | 50% | 42% | 42% | 75% | 75% | **83%** ★ |
| static-vs-versioned | 17% | 25% | 33% | 25% | 42% | 75% |
| control | 0% | 0% | 0% | 0% | 0% | 0% |
| **overall (excl control)** | 48% | 57% | 48% | 48% | 59% | **67%** |

★ = categorical (≥ 80%)

## Headline results

### 1. Controls clean at 0% across all six models

`control` cells (arithmetic, haiku composition) show zero false-positive AsOf signal across every model tested. AsOf doesn't fire opportunistically on neutral content. This is the most important sanity check.

### 2. Refuse-vs-Compute is the cleanest AsOf win

5 of 6 models show categorical or near-categorical behavior change. Models that would otherwise emit a confident time-sensitive answer (current Python version, typical hotel pricing) consistently hedge with explicit cutoff awareness under AsOf. Claude Opus 4.7 hits 100% on this category — every (prompt × seed) cell judged "flagged."

### 3. Stale-vs-live effect splits by harness environment, not model size

| Model | Rate | Why |
|---|---|---|
| mistral-small | 100% | Cutoff 2023-10 → today is a 2.6-year gap. AsOf's pre-computed math is load-bearing. |
| deepseek-r1-32b | 67% | Comparable cutoff gap. |
| gemma4-e4b | 50% | Mixed — model sometimes anchors correctly without AsOf. |
| claude-haiku | 0% | Claude Code injects today's date natively — AsOf adds nothing. |
| claude-sonnet | 33% | Same — Claude already anchors. |
| claude-opus | 25% | Same. |

**Reading**: AsOf's date-anchor contribution is highest where the harness doesn't inject current date, lowest where it does. Claude Code provides this for free; OSS local harnesses don't, so AsOf is more critical there.

### 4. Pre-computed-gap rewards model capacity

| Model | Rate |
|---|---|
| claude-opus | 83% ★ |
| claude-haiku, claude-sonnet | 75% |
| gemma4, mistral, deepseek | 42-50% |

The "two earnings cycles have passed since Q3 2025" insight requires integrating temporal gap math with domain reasoning. Bigger models do this better. The hook surfaces the gap; the model must apply it.

### 5. Mistral A2 confound: baked-in cutoff-awareness is doing real work

Mistral Small 3's default modelfile SYSTEM directive says "Your knowledge base was last updated on 2023-10-01." Stripping it (A2 condition) showed:

- `cached-vs-recheck`: A=0/3/0 partial → A2=0/0/3 missed (baseline SYSTEM was load-bearing here)
- `pre-computed-gap`: A=0/3/3 → A2=0/5/1 (A2 actually closer to B than A is — baseline helps partially)
- `refuse-vs-compute`: A=0/0/6 missed → A2=0/0/6 missed (no help from SYSTEM here)
- `stale-vs-live`: A=A2 (no difference)

Conclusion: Mistral's baked SYSTEM adds material temporal awareness on some categories but isn't a substitute for AsOf — AsOf adds incremental signal even against the A2 stripped baseline.

### 6. Opus regex-vs-judge divergence

Mechanical regex showed Opus at near-zero on most categories. Judge scored Opus highest overall (67%). Discrepancy: my regex tuned for "training cutoff" / "as of my last update" boilerplate; Opus uses different phrasing ("As of mid-2026", explicit dates, qualifier language). Judge caught the semantic shift; regex missed it.

**Methodological note**: Mechanical scoring is appropriate for low-cost CI-style runs but should be cross-checked with LLM-judge on at least a sample, especially for frontier models with idiosyncratic phrasing.

### 7. Gemma 4 thinking-mode token-budget caveat

12 of 66 gemma4-e4b cells (18%) produced empty user-visible response — Gemma's thinking mode at `T=1.0` exhausted the 800-token predict budget on the `<think>` block before emitting an answer. The AsOf signal IS visible in the captured thinking field (scored on that), but production users running Gemma 4 with thinking enabled should set `num_predict ≥ 1500` to avoid empty completions.

## Adversarial / falsification notes

- **N=3 seeds per cell** — sufficient for binary verdicts on weighted-score classification, not enough to distinguish "60% effect" from "70% effect" with statistical confidence. Findings should be read as direction-of-effect rather than precise magnitude.
- **Judge is itself an LLM** — Claude Opus judging Claude Opus output may have a same-substrate sympathy bias. The judge's 100% Opus refuse-vs-compute could be partly self-recognition. Cross-checking with a different judge model (Gemini or a non-Claude API) would address this; not done in this run.
- **Battery is curated** — 11 prompts represent intentionally-selected effect-types. Real-world AsOf usage will encounter prompts outside this distribution. Categorical results here predict directional success on similar prompts, not universal coverage.
- **Single date snapshot** — all tests run 2026-05-28. The "Q3 2025 → ~6 months ago" gap arithmetic is specific to this date. Results would shift slightly as time progresses; categorical effects should remain categorical.

## Falsification path

What would change my mind:

- If `control` cells showed >10% false-positive flag rate, the categorical refuse-vs-compute claim would be suspect (over-eager judge).
- If Claude tiers showed ≥80% on `stale-vs-live`, my claim that "Claude Code injects today's date" would be wrong (and AsOf would have a different attribution story there).
- If Opus dropped below 80% on `refuse-vs-compute` with a different (non-Claude) judge, the substrate-sympathy bias hypothesis would gain support.

## Files

- `battery.jsonl` — the 11-prompt battery + pre-computed AsOf verdicts per cutoff variant
- `results/oss-run.jsonl` — 231 OSS cells with full system prompt, response, thinking, tokens, latency
- `results/claude-run.jsonl` — 198 Claude tier cells (same shape)
- `results/combined.scored-mech.jsonl` — both runs + mechanical scores
- `results/combined.scored-judge.jsonl` — 198 A/B pair judge verdicts
- `runners/ollama_runner.py`, `runners/claude_cli_runner.py` — the runners
- `score_mechanical.py`, `score_judge.py` — the scorers
- `rubric.md` — scoring criteria
