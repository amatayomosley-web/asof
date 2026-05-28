"""LLM-judge scoring pass.

For each (prompt, condition-pair) — pairs A vs B for the same prompt+seed+model —
invokes Claude via the Claude Code CLI to score whether B surfaced the temporal-
staleness concern more than A. Output: {flagged, partial, missed}.

Uses `claude -p --model opus --output-format json --setting-sources local` in a
clean tmp CWD, mirroring the clean-Claude-phase methodology.

Run:
  python tests/abtests/score_judge.py --in tests/abtests/results/oss-run.scored-mech.jsonl
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path


CATEGORY_DESCRIPTIONS = {
    "refuse-vs-compute": "Whether the model refuses or hedges on time-sensitive facts (e.g., 'current Python version', 'typical hotel price') instead of giving a confident guess from training-cutoff data.",
    "stale-vs-live": "Whether arithmetic involving elapsed time (e.g., 'started March 2024, what's the balance today') is anchored to today's date rather than the training cutoff.",
    "cached-vs-recheck": "Whether the model recognizes that a previously-read file has been externally modified and would need to be re-read before grounding claims on its content.",
    "pre-computed-gap": "Whether the model uses the pre-computed gap math (e.g., 'two earnings cycles have passed since Q3 2025') rather than redoing the date arithmetic itself.",
    "static-vs-versioned": "Whether the model recognizes that software APIs / named-year events / versioned data may have changed since training cutoff.",
    "control": "Control prompt — AsOf should produce no behavior change. Score 'missed' is the correct outcome here.",
}


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_pairs(rows: list[dict]) -> list[dict]:
    """For each (model, prompt_id, seed), pair the A row with the B row."""
    by_key: dict[tuple, dict] = {}
    for r in rows:
        if "error" in r:
            continue
        key = (r.get("model"), r.get("prompt_id"), r.get("seed"), r.get("condition"))
        by_key[key] = r

    pairs = []
    keys_seen = set()
    for (model, pid, seed, cond), row in by_key.items():
        if cond != "A":
            continue
        b_key = (model, pid, seed, "B")
        if b_key in by_key:
            pairs.append({
                "model": model,
                "prompt_id": pid,
                "seed": seed,
                "category": row.get("category"),
                "row_a": row,
                "row_b": by_key[b_key],
            })
            keys_seen.add(b_key)
    return pairs


def call_judge(pair: dict, cwd: Path, timeout: int = 120) -> dict:
    """Call Claude Opus to score this A/B pair."""
    cat = pair["category"]
    cat_desc = CATEGORY_DESCRIPTIONS.get(cat, "(uncategorized)")

    prompt = f"""You are scoring whether response_B surfaced the temporal-staleness concern relevant to this prompt more than response_A. The two responses are from the same model answering the same user prompt; response_B was given the AsOf temporal-awareness hook output in its system context, response_A was not.

Category: {cat}
What this category tests: {cat_desc}

User prompt:
{pair['row_a']['user_message']}

Response A (no AsOf):
{pair['row_a']['response']}

Response B (with AsOf):
{pair['row_b']['response']}

Score B's behavior change over A in ONE WORD: "flagged", "partial", or "missed".
- "flagged" — B clearly surfaces the temporal concern (explicit refusal, hedging with cutoff awareness, gap arithmetic, version awareness) in a way A did not
- "partial" — B mentions the concern but ambiguously, OR A already did some of this independently
- "missed" — B does not surface the concern; B's behavior is indistinguishable from A on this axis

Reply with only one word: flagged, partial, or missed."""

    cmd = [
        "claude", "-p", prompt,
        "--model", "opus",
        "--output-format", "json",
        "--tools", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--setting-sources", "local",
        "--system-prompt", "You are a careful judge. Reply with exactly one word from {flagged, partial, missed}.",
    ]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"verdict": "error", "error": f"timeout {timeout}s", "latency_ms": timeout*1000}

    latency_ms = int((time.time() - t0) * 1000)
    if r.returncode != 0:
        return {"verdict": "error", "error": f"exit {r.returncode}: {r.stderr[:200]}", "latency_ms": latency_ms}
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        return {"verdict": "error", "error": f"parse: {e}", "latency_ms": latency_ms}

    raw = (data.get("result", "") or "").strip().lower()
    # Be lenient — take the first known token
    verdict = "error"
    for v in ("flagged", "partial", "missed"):
        if v in raw[:30]:
            verdict = v
            break
    return {
        "verdict": verdict,
        "raw_response": raw[:200],
        "latency_ms": latency_ms,
        "cost_usd": data.get("total_cost_usd", 0),
        "error": None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None, help="Limit number of pairs to judge (debug)")
    args = ap.parse_args()

    out_path = args.out or args.inp.with_name(args.inp.stem + ".scored-judge.jsonl")
    rows = load_rows(args.inp)
    pairs = build_pairs(rows)

    if args.limit:
        pairs = pairs[:args.limit]

    print(f"Judging {len(pairs)} A/B pairs")

    cwd = Path(tempfile.mkdtemp(prefix="asof-judge-"))
    # Append-only output. Resume if rerun.
    done = set()
    if out_path.is_file():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    done.add((d["model"], d["prompt_id"], d["seed"]))
                except (json.JSONDecodeError, KeyError):
                    continue
    print(f"Already judged: {len(done)} pairs")

    total_cost = 0.0
    by_judge = defaultdict(int)
    for i, pair in enumerate(pairs, 1):
        key = (pair["model"], pair["prompt_id"], pair["seed"])
        if key in done:
            continue
        res = call_judge(pair, cwd)
        out_row = {
            "model": pair["model"],
            "prompt_id": pair["prompt_id"],
            "seed": pair["seed"],
            "category": pair["category"],
            "judge_score": res["verdict"],
            "judge_raw": res.get("raw_response", ""),
            "judge_latency_ms": res["latency_ms"],
            "judge_cost_usd": res.get("cost_usd", 0),
        }
        if res.get("error"):
            out_row["judge_error"] = res["error"]
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(out_row) + "\n")
        total_cost += res.get("cost_usd", 0) or 0
        by_judge[res["verdict"]] += 1
        print(f"  [{i:3d}/{len(pairs)}] {pair['model']:22s} {pair['prompt_id']:4s} seed={pair['seed']}  {res['verdict']:10s} ({res['latency_ms']}ms)", flush=True)

    print()
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Verdict distribution: {dict(by_judge)}")


if __name__ == "__main__":
    main()
