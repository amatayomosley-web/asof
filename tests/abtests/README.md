# A/B test battery

Empirical evaluation of AsOf's effect on stale-context staling, across local OSS models and Claude tiers.

## Battery

11 prompts across 5 effect-types + controls (see `build_battery.py`):

| Effect type | Tests | Prompts |
|---|---|---|
| `refuse-vs-compute` | Whether the model refuses/hedges instead of guessing time-sensitive facts | P1, P2 |
| `stale-vs-live` | Whether elapsed-time arithmetic is anchored to today (not training cutoff) | P3, P4 |
| `cached-vs-recheck` | Whether the model re-verifies cached file content after external edit | P5 |
| `pre-computed-gap` | Whether the model uses pre-computed gap math rather than redoing it | P6, P7 |
| `static-vs-versioned` | Whether the model recognizes API versions / dated events may have changed | P8, P9 |
| `control` | AsOf should produce no behavior change | P10, P11 |

## Conditions

- **A** — bare prompt, no AsOf
- **B** — AsOf primer + session_init + per-prompt watch verdict in system
- **A2** (Mistral Small only) — bare with default modelfile SYSTEM stripped (isolates the model's baked-in cutoff-awareness)

## Models

| Model | Size | Notes |
|---|---|---|
| `gemma4:e4b` | 9.6 GB | Thinking mode enabled via `<\|think\|>` token |
| `mistral-small:latest` | 14 GB | Has baked-in cutoff-awareness (modelfile SYSTEM directive) |
| `deepseek-r1:32b` | 19 GB | Reasoning model — thinking ON by default, `num_ctx=4096` to fit VRAM |
| `claude-haiku-4-5` | API | Clean `claude -p` subprocess invocation |
| `claude-sonnet-4-6` | API | Clean `claude -p` subprocess invocation |
| `claude-opus-4-7` | API | Clean `claude -p` subprocess invocation |

Each OSS cell runs with `temperature=0` for greedy decoding (single seed sufficient given determinism). Claude cells default-temperature.

## Reproduce

```bash
# 1. Build battery (one-off; pre-computes per-cutoff verdicts)
python tests/abtests/build_battery.py

# 2. OSS phase (sequential, ~2 hrs end-to-end)
python tests/abtests/runners/ollama_runner.py \
  --battery tests/abtests/battery.jsonl \
  --out tests/abtests/results/oss-run.jsonl

# 3. Claude phase (clean subscription-CLI, ~30 min)
python tests/abtests/runners/claude_cli_runner.py \
  --battery tests/abtests/battery.jsonl \
  --out tests/abtests/results/claude-run.jsonl

# 4. Mechanical scoring
python tests/abtests/score_mechanical.py --in tests/abtests/results/oss-run.jsonl
python tests/abtests/score_mechanical.py --in tests/abtests/results/claude-run.jsonl

# 5. LLM-judge scoring (Claude Opus, ~$5)
python tests/abtests/score_judge.py --in tests/abtests/results/oss-run.scored-mech.jsonl
python tests/abtests/score_judge.py --in tests/abtests/results/claude-run.scored-mech.jsonl
```

## Results

Per-run JSONL stored under `results/` (gitignored — too large for repo). Summary aggregates in `findings.md` (committed).
