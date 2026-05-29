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

    # --- Step 2: external mutation (someone edits the file) — SKIPPED for the
    #     control, which proves AsOf does not false-fire on an unchanged file ---
    if condition != "control":
        cfg.write_text(f"DEPLOY_TOKEN={NEW_TOKEN}\n", encoding="utf-8")  # different length

    # --- Step 3: AsOf's REAL detection/render. B and control both have AsOf
    #     ON; A is the bare baseline. For control the file is unchanged, so a
    #     correct detector returns nothing and asof_block stays empty. ---
    asof_block = ""
    if condition in ("B", "control", "implied"):
        records = _load_tool_log(log_dir / f"{session_id}.jsonl")
        stale = _evaluate_working_set(records)
        if stale:
            asof_block = render_watch_block(current_dt=now, stale_files=stale)
            if condition == "implied":
                # Strip the re-read imperative -> the pre-fix "status only"
                # verdict, for the reg/implied/directive framing comparison.
                asof_block = "\n".join(
                    ln for ln in asof_block.splitlines()
                    if not ln.startswith("Re-read any file below"))

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

    if condition == "control":
        # File never changed: AsOf must stay silent (no false positive).
        verdict = "false_fire" if asof_block else "clean"
    else:
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
        "final": resp[:800],
    }


def ollama_model_fn(model: str = "deepseek-r1:32b", *, think: bool = True,
                    num_ctx: int = 4096, num_predict: int = 4096,
                    temperature: float = 0.6, extra_options: dict | None = None,
                    url: str = "http://localhost:11434") -> ModelFn:
    # num_ctx 4096: the prompts here are small, and findings.md notes
    # deepseek-r1:32b needs this ceiling on a 24 GB GPU to avoid OOM.
    """Build a model_fn that calls Ollama's /api/chat (DeepSeek-R1 by default)."""
    import requests

    def _fn(messages: Messages) -> str:
        opts = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict}
        if extra_options:
            opts.update(extra_options)
        payload = {"model": model, "messages": messages, "stream": False,
                   "think": think, "options": opts}
        r = requests.post(f"{url}/api/chat", json=payload, timeout=3600)
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "") or ""

    return _fn


# The three local OSS models, with the sampling each was run at in findings.md.
OSS_MODELS = [
    {"tag": "deepseek-r1:32b",
     "opts": dict(think=True, temperature=0.6, num_ctx=4096, num_predict=4096)},
    {"tag": "gemma4:e4b",
     "opts": dict(think=True, temperature=1.0, num_ctx=8192, num_predict=2048,
                  extra_options={"top_p": 0.95, "top_k": 64})},
    {"tag": "mistral-small:latest",
     "opts": dict(think=False, temperature=0.15, num_ctx=8192, num_predict=2048)},
]
CONDITIONS = ("A", "B", "control")

# Smaller models for the framing comparison (size ladder, official tags).
SMALLER_MODELS = [
    {"tag": "gemma2:2b",   "opts": dict(think=False, temperature=0.7, num_ctx=8192, num_predict=1024)},
    {"tag": "qwen2.5:7b",  "opts": dict(think=False, temperature=0.7, num_ctx=8192, num_predict=1024)},
    {"tag": "llama3.1:8b", "opts": dict(think=False, temperature=0.7, num_ctx=8192, num_predict=1024)},
    {"tag": "gemma2:9b",   "opts": dict(think=False, temperature=0.7, num_ctx=8192, num_predict=1024)},
    {"tag": "qwen2.5:14b", "opts": dict(think=False, temperature=0.7, num_ctx=8192, num_predict=1024)},
]
# Verdict framings: reg = no verdict (baseline); implied = terse status-only
# verdict; directive = status + re-read imperative (current default).
# Each is (label, run_cell condition).
FRAMINGS = [("reg", "A"), ("implied", "implied"), ("directive", "B")]


def _free_loaded_models() -> None:
    """Unload whatever Ollama has in VRAM — only one of these models fits on a
    24 GB box at a time, so we run them strictly sequentially."""
    import subprocess
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True,
                             timeout=30).stdout
        for line in out.splitlines()[1:]:  # skip header
            parts = line.split()
            if parts:
                subprocess.run(["ollama", "stop", parts[0]], capture_output=True, timeout=120)
    except Exception:
        pass


def main() -> int:
    import tempfile
    ap = argparse.ArgumentParser(description="AsOf live file-staleness test")
    ap.add_argument("--mode", choices=["ab", "framing"], default="ab",
                    help="ab: A/B/control on the OSS set; framing: reg/implied/directive on smaller models")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.mode == "framing":
        models, cells = SMALLER_MODELS, FRAMINGS
        out_path = args.out or "tests/abtests/results/live-staleness-framing.jsonl"
    else:
        models, cells = OSS_MODELS, [(c, c) for c in CONDITIONS]
        out_path = args.out or "tests/abtests/results/live-staleness-oss.jsonl"

    base = Path(tempfile.mkdtemp(prefix="asof_live_"))
    rows: list[dict] = []
    for m in models:
        _free_loaded_models()  # one model in VRAM at a time
        print(f"\n##### {m['tag']} #####")
        model_fn = ollama_model_fn(m["tag"], **m["opts"])
        for seed in range(args.seeds):
            for label, cond in cells:
                safe = m["tag"].replace(":", "_").replace("/", "_")
                ws = base / f"{safe}_s{seed}_{label}"
                sid = f"live-{safe}-{seed}-{label}"
                try:
                    out = run_cell(model_fn, cond, workspace=ws, session_id=sid)
                except Exception as e:
                    out = {"condition": cond, "asof_fired": None, "reread": None,
                           "verdict": "error", "final": str(e)[:300]}
                out.update({"seed": seed, "model": m["tag"], "framing": label})
                rows.append(out)
                print(f"  s{seed} {label}: {out['verdict']} "
                      f"(reread={out['reread']}, fired={out['asof_fired']})")
        # Write incrementally so a long multi-model run isn't lost on a crash.
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    _free_loaded_models()

    print("\n================ SUMMARY ================")
    for m in models:
        mr = [r for r in rows if r["model"] == m["tag"]]
        if args.mode == "framing":
            def fresh(label):
                c = [r for r in mr if r.get("framing") == label]
                return f"{sum(1 for r in c if r['verdict']=='fresh')}/{len(c)}"
            print(f"  {m['tag']:16s}  reg {fresh('reg')}   implied {fresh('implied')}"
                  f"   directive {fresh('directive')}")
        else:
            def rate(cond, pred):
                c = [r for r in mr if r["condition"] == cond]
                return f"{sum(1 for r in c if pred(r))}/{len(c)}"
            print(f"  {m['tag']:22s}  A fresh {rate('A', lambda r: r['verdict']=='fresh')}"
                  f"   B fresh {rate('B', lambda r: r['verdict']=='fresh')}"
                  f"   control clean {rate('control', lambda r: r['verdict']=='clean')}")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
