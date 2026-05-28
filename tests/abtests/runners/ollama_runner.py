"""Ollama runner for the AsOf A/B test battery.

For each model × prompt × condition × seed:
  - Assemble the system prompt (primer + session_init + per-prompt verdict for B/A2; empty/default for A)
  - Call Ollama /api/chat with options.seed and options.temperature=0
  - Capture response + path-verification flags
  - Append a JSONL row per call

Sequential model loading: load model 1, run all its cells, unload (keep_alive: 0),
load model 2, etc. Required by 24GB VRAM constraint.

Run:
  python tests/abtests/runners/ollama_runner.py --battery tests/abtests/battery.jsonl --out tests/abtests/results/oss-run.jsonl

Resumability: if --out file exists, prior rows are loaded; cells already present
(model, prompt_id, condition, seed) are skipped. Append-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make asof_core importable from any CWD
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from asof_core.hooks.session_init import session_init
from asof_core.cutoffs import lookup_cutoff


OLLAMA_URL = "http://localhost:11434/api/chat"

# Model spec — display id, ollama tag, cutoff-variant-key in battery, thinking-mode flavor.
# Per-model sampling params follow vendor-recommended defaults (see web research 2026-05-28).
MODELS = [
    {
        "id": "gemma4-e4b",
        "ollama_tag": "gemma4:e4b",            # official tag (user's gemma4-e4b:latest has broken chat template)
        "cutoff_variant": "gemma4-e4b",
        "thinking_mode": "gemma",              # prepend <|think|> to system content; ollama exposes thinking in message.thinking
        "asof_placement": "system",            # primer/verdict goes in system message
        "num_ctx": 8192,
        "num_predict": 800,
        "temperature": 1.0,                    # Google rec: T=1.0, topP=0.95, topK=64
        "top_p": 0.95,
        "top_k": 64,
    },
    {
        "id": "mistral-small",
        "ollama_tag": "mistral-small:latest",
        "cutoff_variant": "mistral-small",
        "thinking_mode": None,
        "asof_placement": "system",
        "num_ctx": 8192,
        "num_predict": 600,
        "temperature": 0.15,                   # Mistral docs rec; also in modelfile default
    },
    {
        "id": "deepseek-r1-32b",
        "ollama_tag": "deepseek-r1:32b",
        "cutoff_variant": "deepseek-r1",
        "thinking_mode": "deepseek",           # default-on; ollama exposes thinking in message.thinking
        "asof_placement": "user",              # DeepSeek docs: avoid system prompt; put instructions in user
        "num_ctx": 4096,                       # reduced to fit 19GB model in 24GB VRAM
        "num_predict": 1200,
        "temperature": 0.6,                    # DeepSeek rec: 0.5-0.7, 0.6 optimal; don't set top_p with temp
    },
]


def load_battery(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def assemble_asof_block(row: dict, model_spec: dict, now: datetime) -> str:
    """Build the AsOf content (primer + session_init + verdict).
    Used in either system or user message depending on model_spec['asof_placement']."""
    cutoff_variant = model_spec["cutoff_variant"]
    init_block = session_init(model_id=model_spec["id"], now=now)
    primer = row["primer"]
    verdict = row["verdicts"].get(cutoff_variant) or row["verdicts"].get("default", "")

    parts = []
    if model_spec.get("thinking_mode") == "gemma":
        parts.append("<|think|>")
    parts.append(primer)
    parts.append("")
    parts.append(init_block.strip())
    if verdict.strip():
        parts.append("")
        parts.append(verdict.strip())
    return "\n".join(parts)


def assemble_messages(row: dict, model_spec: dict, condition: str, now: datetime) -> list[dict]:
    """Build the full message list for a given (prompt, model, condition).
    Honors model_spec['asof_placement'] = 'system' or 'user' for the B condition.

    Conditions:
      A   — bare (no system, default modelfile SYSTEM may apply for some models)
      A2  — empty system explicitly sent (strips modelfile SYSTEM, mistral-only)
      B   — AsOf primer + verdict in system OR prefixed into user message per spec
    """
    placement = model_spec.get("asof_placement", "system")
    user_msg = row["prompt"]

    if condition == "A":
        return [{"role": "user", "content": user_msg}]
    if condition == "A2":
        return [{"role": "system", "content": ""}, {"role": "user", "content": user_msg}]

    # Condition B
    asof_text = assemble_asof_block(row, model_spec, now)
    if placement == "user":
        return [{"role": "user", "content": asof_text + "\n\n---\n\n" + user_msg}]
    return [{"role": "system", "content": asof_text}, {"role": "user", "content": user_msg}]


def call_ollama(model_tag: str, messages: list[dict], seed: int,
                keep_alive, *,
                options: Optional[dict] = None, timeout: int = 300) -> dict:
    """Make a single Ollama /api/chat call. Returns the JSON response dict
    plus latency_ms and any error."""
    body = {
        "model": model_tag,
        "messages": messages,
        "stream": False,
        "keep_alive": keep_alive,
        "options": options or {
            "temperature": 0,
            "seed": seed,
        },
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            latency_ms = int((time.time() - t0) * 1000)
            return {"data": data, "latency_ms": latency_ms, "error": None}
    except urllib.error.HTTPError as e:
        latency_ms = int((time.time() - t0) * 1000)
        body = ""
        try:
            body = e.read().decode("utf-8")[:500]
        except Exception:
            pass
        return {"data": None, "latency_ms": latency_ms, "error": f"HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {"data": None, "latency_ms": latency_ms, "error": str(e)}
    except (json.JSONDecodeError, ValueError) as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {"data": None, "latency_ms": latency_ms, "error": f"parse: {e}"}


def verify_path(system_text: str, response_text: str, condition: str) -> dict:
    """Path-verification checks per cairn discipline 2026-05-01.

    Returns a dict of pass/fail booleans + reasons.
    """
    checks = {}
    if condition == "B":
        checks["primer_in_system"] = "AsOf" in system_text and "verdict" in system_text.lower()
    else:
        checks["primer_in_system"] = True  # Not expected
    checks["non_empty_response"] = len(response_text.strip()) > 0
    return checks


def detect_thinking(response_text: str) -> dict:
    """Detect thinking-mode tokens in response. Heuristic — model-specific."""
    return {
        "has_think_block": "<think>" in response_text and "</think>" in response_text,
        "has_channel_block": "<|channel" in response_text,
    }


def already_done(out_path: Path) -> set[tuple]:
    """Read existing JSONL and return the set of (model, prompt_id, condition, seed) keys."""
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


def run_model(model_spec: dict, battery: list[dict], out_path: Path,
              seeds: list[int], done: set[tuple]) -> dict:
    """Run all cells for one model, then unload it."""
    model_id = model_spec["id"]
    ollama_tag = model_spec["ollama_tag"]

    print(f"\n=== {model_id} ({ollama_tag}) ===", flush=True)
    t_start = time.time()

    # Conditions per model. Mistral gets A2 in addition.
    conditions = ["A", "B"]
    if model_id == "mistral-small":
        conditions = ["A", "A2", "B"]

    cells = []
    for row in battery:
        for cond in conditions:
            for seed in seeds:
                key = (model_id, row["id"], cond, seed)
                if key not in done:
                    cells.append((row, cond, seed))

    if not cells:
        print(f"  (all cells already done; skipping)", flush=True)
        return {"model": model_id, "completed": 0, "elapsed_s": 0}

    total = len(cells)
    print(f"  cells to run: {total}", flush=True)
    completed = 0
    failures = 0

    for i, (row, cond, seed) in enumerate(cells, 1):
        # Last cell unloads the model; otherwise keep it warm.
        keep_alive = 0 if i == total else -1

        now = datetime.now(timezone.utc)
        messages = assemble_messages(row, model_spec, cond, now)

        opts = {
            "temperature": model_spec.get("temperature", 0),
            "seed": seed,
            "num_ctx": model_spec.get("num_ctx", 8192),
            "num_predict": model_spec.get("num_predict", 800),
        }
        if "top_p" in model_spec:
            opts["top_p"] = model_spec["top_p"]
        if "top_k" in model_spec:
            opts["top_k"] = model_spec["top_k"]
        result = call_ollama(ollama_tag, messages, seed, keep_alive, options=opts)
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
            print(f"  [{i:3d}/{total}] {row['id']} {cond} seed={seed}  ERROR {result['error'][:60]}", flush=True)
            continue

        data = result["data"]
        msg = data.get("message", {})
        response_text = msg.get("content", "")
        thinking_text = msg.get("thinking", "")  # ollama exposes this when available

        # Concatenate full system+user text for path checks
        sys_text = next((m["content"] for m in messages if m["role"] == "system"), "")
        usr_text = next((m["content"] for m in messages if m["role"] == "user"), "")
        full_input = sys_text + "\n" + usr_text
        checks = verify_path(full_input, response_text, cond)
        thinking = detect_thinking(response_text)

        out_row = {
            "ts": now.isoformat(),
            "model": model_id,
            "prompt_id": row["id"],
            "category": row["category"],
            "condition": cond,
            "seed": seed,
            "system_prompt": sys_text,
            "user_message": usr_text,
            "response": response_text,
            "thinking": thinking_text,
            "thinking_detected": thinking,
            "path_checks": checks,
            "tokens_in": data.get("prompt_eval_count", 0),
            "tokens_out": data.get("eval_count", 0),
            "latency_ms": result["latency_ms"],
            "temperature": opts.get("temperature"),
            "asof_placement": model_spec.get("asof_placement"),
        }
        write_row(out_path, out_row)
        completed += 1
        print(f"  [{i:3d}/{total}] {row['id']} {cond} seed={seed}  ok ({result['latency_ms']}ms, {data.get('eval_count', 0)} tok)", flush=True)

    elapsed = time.time() - t_start
    print(f"  done: {completed}/{total} cells in {elapsed/60:.1f} min, {failures} failures", flush=True)
    return {"model": model_id, "completed": completed, "failures": failures, "elapsed_s": elapsed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--battery", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seeds", default="0,1,2", help="Comma-separated seeds")
    ap.add_argument("--models", default=None, help="Comma-separated subset of model IDs to run; default all")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    battery = load_battery(args.battery)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = already_done(args.out)

    selected = MODELS
    if args.models:
        wanted = set(args.models.split(","))
        selected = [m for m in MODELS if m["id"] in wanted]

    print(f"Battery: {len(battery)} prompts", flush=True)
    print(f"Models : {[m['id'] for m in selected]}", flush=True)
    print(f"Seeds  : {seeds}", flush=True)
    print(f"Out    : {args.out}", flush=True)
    print(f"Resume : {len(done)} cells already in output", flush=True)

    summaries = []
    for ms in selected:
        s = run_model(ms, battery, args.out, seeds, done)
        summaries.append(s)

    print("\n=== Summary ===")
    for s in summaries:
        print(f"  {s['model']:20s} {s['completed']:3d} cells, {s.get('failures', 0)} fails, {s.get('elapsed_s', 0)/60:.1f} min")


if __name__ == "__main__":
    main()
