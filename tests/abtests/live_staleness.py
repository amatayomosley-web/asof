"""Live file-staleness harness.

The A/B battery (findings.md) hand-fed AsOf's verdict as static text, so it
only tested "does the model act on a correct signal?" — not whether AsOf
*detects* and *surfaces* staleness in a real loop. This harness closes that
gap for the file-staleness mechanism:

    1. the model reads a config file (we record the read exactly as the
       PostToolUse hook would, via asof_core.post_tool)
    2. an external process mutates the file (the value changes)
    3. the model is asked to use the file's value again, and may re-read

Condition B runs AsOf's REAL detection (`_evaluate_working_set`) + renderer
over the recorded read and the now-changed file, and injects the resulting
STALE block. Condition A gets nothing. The detection is genuine — nothing is
hand-fed.

Scoring is mechanical and judge-free: the file's token is distinctive and
changes length (REL-7741 -> REL-9982-HOTFIX), so a) the size rung detects the
change for free, and b) we can read off whether the model's final answer
commits the STALE token (failure), the FRESH token, or re-read to get it.

`model_fn(messages) -> str` is injected, so the wiring is unit-testable with a
mock; `main()` runs it against deepseek-r1:32b over Ollama for the real test.

Run:  python tests/abtests/live_staleness.py --model deepseek-r1:32b --seeds 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from asof_core.hooks.post_tool import post_tool
from asof_core.hooks.watch import _load_tool_log, _evaluate_working_set
from asof_core.output import render_watch_block

OLD_TOKEN = "REL-7741"
NEW_TOKEN = "REL-9982-HOTFIX"  # deliberately a different length than OLD
CONFIG_NAME = "deploy_config.txt"

Messages = list[dict]
ModelFn = Callable[[Messages], str]


def _system_prompt() -> str:
    return (
        "You are a deployment assistant. You produce a single shell command of the "
        "form `deploy --token <DEPLOY_TOKEN>` using the DEPLOY_TOKEN from the project's "
        f"{CONFIG_NAME}. If you are not certain the value you hold is current, you may "
        f"re-read the file: reply with exactly one line `READ_FILE: {CONFIG_NAME}` and "
        "nothing else, and the current contents will be returned to you."
    )


def _use_prompt(content_at_read: str, asof_block: str) -> str:
    parts = [
        f"Earlier you read {CONFIG_NAME}, which contained:\n\n{content_at_read}\n",
    ]
    if asof_block:
        parts.append(asof_block)
    parts.append(
        f"Now produce the deploy command using the DEPLOY_TOKEN from {CONFIG_NAME}."
    )
    return "\n\n".join(parts)


def run_cell(
    model_fn: ModelFn,
    condition: str,
    *,
    workspace: Path,
    session_id: str,
    now: datetime | None = None,
) -> dict:
    """Run one A or B cell. Returns the scored outcome."""
    now = now or datetime.now(timezone.utc)
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = workspace / CONFIG_NAME
    log_dir = workspace / "asof_log"

    # --- Step 1: the model reads the file; AsOf records the read ---
    cfg.write_text(f"DEPLOY_TOKEN={OLD_TOKEN}\n", encoding="utf-8")
    # Age the read into the past so the later mutation is unambiguously after it
    # (a sub-tolerance read->edit gap would read as "mtime unchanged").
    past = time.time() - 120
    os.utime(cfg, (past, past))
    content_at_read = cfg.read_text(encoding="utf-8").strip()
    post_tool(session_id=session_id, tool_name="Read",
              tool_input={"file_path": str(cfg)}, log_dir=log_dir, now=now)

    # --- Step 2: external mutation (someone edits the file) ---
    cfg.write_text(f"DEPLOY_TOKEN={NEW_TOKEN}\n", encoding="utf-8")  # different length

    # --- Step 3: AsOf's REAL detection/render (condition B only) ---
    asof_block = ""
    if condition == "B":
        records = _load_tool_log(log_dir / f"{session_id}.jsonl")
        stale = _evaluate_working_set(records)
        if stale:
            asof_block = render_watch_block(current_dt=now, stale_files=stale)

    messages: Messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _use_prompt(content_at_read, asof_block)},
    ]
    resp = model_fn(messages)
    reread = bool(re.search(r"READ_FILE\s*:", resp))
    if reread:
        messages.append({"role": "assistant", "content": resp})
        messages.append({
            "role": "user",
            "content": (f"Current contents of {CONFIG_NAME}:\n\n"
                        f"{cfg.read_text(encoding='utf-8').strip()}\n\n"
                        "Now give your final deploy command."),
        })
        resp = model_fn(messages)

    used_new = NEW_TOKEN in resp or "9982" in resp
    used_old = (OLD_TOKEN in resp or "7741" in resp) and not used_new
    if reread or used_new:
        verdict = "fresh"
    elif used_old:
        verdict = "stale"          # the failure AsOf exists to prevent
    else:
        verdict = "ambiguous"      # didn't commit a token (soft non-failure)
    return {
        "condition": condition,
        "asof_fired": bool(asof_block),
        "reread": reread,
        "verdict": verdict,
        "final": resp[:500],
    }


def ollama_model_fn(model: str = "deepseek-r1:32b", *, think: bool = True,
                    num_ctx: int = 4096, num_predict: int = 4096,
                    temperature: float = 0.6, url: str = "http://localhost:11434") -> ModelFn:
    # num_ctx 4096: the prompts here are small, and findings.md notes
    # deepseek-r1:32b needs this ceiling on a 24 GB GPU to avoid OOM.
    """Build a model_fn that calls Ollama's /api/chat (DeepSeek-R1 by default)."""
    import requests

    def _fn(messages: Messages) -> str:
        payload = {
            "model": model, "messages": messages, "stream": False, "think": think,
            "options": {"temperature": temperature, "num_ctx": num_ctx,
                        "num_predict": num_predict},
        }
        r = requests.post(f"{url}/api/chat", json=payload, timeout=3600)
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "") or ""

    return _fn


def main() -> int:
    ap = argparse.ArgumentParser(description="AsOf live file-staleness test")
    ap.add_argument("--model", default="deepseek-r1:32b")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import tempfile
    model_fn = ollama_model_fn(args.model)
    rows: list[dict] = []
    base = Path(tempfile.mkdtemp(prefix="asof_live_"))
    for seed in range(args.seeds):
        for cond in ("A", "B"):
            ws = base / f"s{seed}_{cond}"
            sid = f"live-{seed}-{cond}"
            out = run_cell(model_fn, cond, workspace=ws, session_id=sid)
            out.update({"seed": seed, "model": args.model})
            rows.append(out)
            print(f"  seed{seed} {cond}: verdict={out['verdict']} reread={out['reread']} "
                  f"asof_fired={out['asof_fired']}")

    def rate(cond: str) -> str:
        cells = [r for r in rows if r["condition"] == cond]
        fresh = sum(1 for r in cells if r["verdict"] == "fresh")
        return f"{fresh}/{len(cells)} fresh"
    print(f"\nA (no AsOf): {rate('A')}    B (AsOf): {rate('B')}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
