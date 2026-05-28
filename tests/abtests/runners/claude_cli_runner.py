"""Claude Code subscription-CLI runner for the AsOf A/B test battery.

Spawns fresh `claude -p` subprocesses per (prompt, condition, seed, tier).
Each invocation:
  - Runs in a clean tmp CWD (no nearby CLAUDE.md to load)
  - --setting-sources local (skips user/project cairn config)
  - --disable-slash-commands (no skills)
  - --tools "" (no tool use)
  - --no-session-persistence (don't pollute session history)
  - --system-prompt <primer+session_init+verdict | empty> (override default)
  - --output-format json (structured response)

Models: opus, sonnet, haiku (Claude Code tier aliases).
3 seeds per cell — but Claude CLI doesn't accept a seed parameter, so seed
varies only by adding a tiny semantic hash into the user message; reality
is that temperature 1 default produces some variance anyway.

Run:
  python tests/abtests/runners/claude_cli_runner.py --battery tests/abtests/battery.jsonl --out tests/abtests/results/claude-run.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make asof_core importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from asof_core.hooks.session_init import session_init
from asof_core.cutoffs import lookup_cutoff


TIERS = [
    {"id": "haiku",  "alias": "haiku",  "cutoff_variant": "claude-haiku-4-5"},
    {"id": "sonnet", "alias": "sonnet", "cutoff_variant": "claude-sonnet-4-6"},
    {"id": "opus",   "alias": "opus",   "cutoff_variant": "claude-opus-4-7"},
]


def load_battery(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def assemble_system_prompt(row: dict, tier: dict, condition: str, now: datetime) -> str:
    """Build the system prompt. Same as ollama runner conceptually."""
    if condition == "A":
        # Clean baseline — Claude Code's default empty system, but we need something
        # to force it past the workspace-trust dialog. Use a benign minimal instruction.
        return "You are a helpful assistant. Answer the user's question directly."

    # Condition B
    cutoff_variant = tier["cutoff_variant"]
    init_block = session_init(model_id=cutoff_variant, now=now)
    primer = row["primer"]
    verdict = row["verdicts"].get(cutoff_variant) or row["verdicts"].get("default", "")

    parts = [primer, "", init_block.strip()]
    if verdict.strip():
        parts.append("")
        parts.append(verdict.strip())
    return "\n".join(parts)


def already_done(out_path: Path) -> set[tuple]:
    if not out_path.is_file():
        return set()
    done = set()
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                done.add((d["model"], d["prompt_id"], d["condition"], d["seed"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def write_row(out_path: Path, row: dict) -> None:
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def call_claude(model_alias: str, system_prompt: str, user_text: str, cwd: Path,
                timeout: int = 180) -> dict:
    """One claude -p invocation. Returns {data, latency_ms, error}."""
    cmd = [
        "claude",
        "-p", user_text,
        "--model", model_alias,
        "--output-format", "json",
        "--tools", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--setting-sources", "local",
        "--system-prompt", system_prompt,
    ]
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {"data": None, "latency_ms": latency_ms, "error": f"timeout after {timeout}s"}

    latency_ms = int((time.time() - t0) * 1000)
    if result.returncode != 0:
        return {"data": None, "latency_ms": latency_ms,
                "error": f"exit {result.returncode}: {result.stderr[:300]}"}
    try:
        data = json.loads(result.stdout)
        return {"data": data, "latency_ms": latency_ms, "error": None}
    except json.JSONDecodeError as e:
        return {"data": None, "latency_ms": latency_ms,
                "error": f"parse: {e}; stdout[:200]={result.stdout[:200]}"}


def run_tier(tier: dict, battery: list[dict], out_path: Path,
             seeds: list[int], done: set[tuple], cwd: Path) -> dict:
    """Run all cells for one tier."""
    model_id = f"claude-{tier['id']}"
    print(f"\n=== {model_id} ===", flush=True)
    t_start = time.time()

    cells = []
    for row in battery:
        for cond in ["A", "B"]:
            for seed in seeds:
                key = (model_id, row["id"], cond, seed)
                if key not in done:
                    cells.append((row, cond, seed))

    if not cells:
        print("  (all cells already done)", flush=True)
        return {"model": model_id, "completed": 0, "elapsed_s": 0}

    total = len(cells)
    print(f"  cells to run: {total}", flush=True)
    completed = 0
    failures = 0

    for i, (row, cond, seed) in enumerate(cells, 1):
        now = datetime.now(timezone.utc)
        system_text = assemble_system_prompt(row, tier, cond, now)
        # Seed is encoded as a no-op suffix to nudge tokenization-level variance;
        # Claude CLI doesn't expose a seed param. (Temperature 1 default gives variance anyway.)
        seed_suffix = "" if seed == 0 else f" [seed-marker:{seed}]"
        user_text = row["prompt"] + seed_suffix

        result = call_claude(tier["alias"], system_text, user_text, cwd)
        if result["error"]:
            failures += 1
            err_row = {
                "ts": now.isoformat(),
                "model": model_id,
                "prompt_id": row["id"],
                "category": row["category"],
                "condition": cond,
                "seed": seed,
                "error": result["error"],
                "latency_ms": result["latency_ms"],
            }
            write_row(out_path, err_row)
            print(f"  [{i:3d}/{total}] {row['id']} {cond} seed={seed}  ERROR {result['error'][:80]}", flush=True)
            continue

        data = result["data"]
        response_text = data.get("result", "")
        usage = data.get("usage", {})

        out_row = {
            "ts": now.isoformat(),
            "model": model_id,
            "prompt_id": row["id"],
            "category": row["category"],
            "condition": cond,
            "seed": seed,
            "system_prompt": system_text,
            "user_message": user_text,
            "response": response_text,
            "thinking": "",  # claude-code doesn't expose extended thinking via -p output
            "thinking_detected": {"has_think_block": False, "has_channel_block": False},
            "path_checks": {
                "primer_in_system": "AsOf" in system_text and "verdict" in system_text.lower() if cond == "B" else True,
                "non_empty_response": len(response_text.strip()) > 0,
            },
            "tokens_in": usage.get("input_tokens", 0),
            "tokens_out": usage.get("output_tokens", 0),
            "latency_ms": result["latency_ms"],
            "total_cost_usd": data.get("total_cost_usd", 0),
        }
        write_row(out_path, out_row)
        completed += 1
        print(f"  [{i:3d}/{total}] {row['id']} {cond} seed={seed}  ok ({result['latency_ms']}ms, {usage.get('output_tokens', 0)} tok)", flush=True)

    elapsed = time.time() - t_start
    print(f"  done: {completed}/{total} cells in {elapsed/60:.1f} min, {failures} failures", flush=True)
    return {"model": model_id, "completed": completed, "failures": failures, "elapsed_s": elapsed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--battery", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--tiers", default=None, help="Comma-separated subset of tier IDs (haiku,sonnet,opus); default all")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    battery = load_battery(args.battery)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = already_done(args.out)

    selected = TIERS
    if args.tiers:
        wanted = set(args.tiers.split(","))
        selected = [t for t in TIERS if t["id"] in wanted]

    # Use an isolated CWD so no project-level CLAUDE.md interferes.
    cwd = Path(tempfile.mkdtemp(prefix="asof-clean-claude-"))
    print(f"Clean CWD: {cwd}", flush=True)
    print(f"Battery : {len(battery)} prompts", flush=True)
    print(f"Tiers   : {[t['id'] for t in selected]}", flush=True)
    print(f"Seeds   : {seeds}", flush=True)
    print(f"Out     : {args.out}", flush=True)
    print(f"Resume  : {len(done)} cells already in output", flush=True)

    summaries = []
    for t in selected:
        s = run_tier(t, battery, args.out, seeds, done, cwd)
        summaries.append(s)

    print("\n=== Summary ===")
    for s in summaries:
        print(f"  {s['model']:25s} {s['completed']:3d} cells, {s.get('failures', 0)} fails, {s.get('elapsed_s', 0)/60:.1f} min")


if __name__ == "__main__":
    main()
