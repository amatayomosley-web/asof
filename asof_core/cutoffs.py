"""Training-cutoff lookup table for AsOf.

Maps model identifiers to their published training-data cutoff dates.
Used to compute "your knowledge is N months old" framing without asking
the model to do the arithmetic itself.

Sources of truth:
- Anthropic model docs (https://docs.anthropic.com)
- Google Gemini model docs
- OpenAI model docs
- Other vendors' published model cards

Maintenance: this table needs occasional updates as new models ship.
Process: when a new model ID appears in production usage, check the
vendor's published cutoff date and add the entry. Default fallback for
unknown models is None (the hook surfaces 'unknown cutoff — assume
time-sensitive claims may be stale').

User override (two layers, both documented for the operator):
- ASOF_TRAINING_CUTOFF env var — a global override for the whole session.
- a `cutoffs` map in ~/.asof/config.json — per-model, the recommended way to
  pin an accurate cutoff for a model AsOf does not recognize:
      {"cutoffs": {"my-local-model:latest": "2024-06"}}
  Both take precedence over the table. Format: YYYY-MM (e.g., "2026-01").

resolve_cutoff() layers these so AsOf can ALWAYS emit a cutoff posture:
env -> config map -> registry -> anchored Ollama modelfile scan -> conservative
"unknown" fallback. The model is never asked its own cutoff (models are
unreliable at self-report); unknowns route the operator to the config map.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


# Cutoff format: "YYYY-MM" — first-of-month is used for arithmetic.
TRAINING_CUTOFFS: dict[str, str] = {
    # Anthropic — Claude family
    "claude-opus-4-8": "2026-01",        # released 2026-05-28; training cutoff Jan 2026 per platform.claude.com models doc
    "claude-opus-4-8[1m]": "2026-01",
    "claude-opus-4-7": "2026-01",
    "claude-opus-4-7[1m]": "2026-01",
    "claude-sonnet-4-6": "2026-01",
    "claude-haiku-4-5": "2025-10",
    "claude-haiku-4-5-20251001": "2025-10",
    "claude-opus-4-6": "2025-09",
    "claude-sonnet-4-20250514": "2025-04",
    "claude-3-5-sonnet-20241022": "2024-04",
    "claude-3-5-haiku-20241022": "2024-07",
    "claude-3-opus-20240229": "2023-08",
    # Google — Gemini family
    "gemini-3.5-flash": "2026-01",   # released 2026-05-19, cutoff Jan 2026 (Current runs this)
    "gemini-2.5-pro": "2025-04",
    "gemini-2.5-flash": "2025-04",
    "gemini-2.0-flash": "2024-08",
    "gemini-1.5-pro": "2024-05",
    # OpenAI — GPT family (best-known cutoffs)
    "gpt-4o": "2023-10",
    "gpt-4o-2024-08-06": "2023-10",
    "gpt-4-turbo": "2023-12",
    "gpt-4": "2023-04",
    "gpt-3.5-turbo": "2021-09",
    # Meta — Llama family
    "llama-3-70b": "2023-12",
    "llama-3.1-405b": "2023-12",
    # Mistral
    "mistral-large": "2023-12",
    "mistral-small": "2023-10",          # per modelfile SYSTEM directive on Ollama mistral-small:latest
    "mistral-small-3": "2023-10",
    "mistral-nemo": "2024-04",           # NVIDIA partnership instruct; verify against vendor docs
    # Google — Gemma family (open weights)
    "gemma4-e4b": "2024-06",             # approximate; verify against Google AI Gemma 4 model card
    "gemma4-e2b": "2024-06",             # approximate; verify against Google AI Gemma 4 model card
    "gemma-3-27b-it": "2024-08",         # approximate; verify against vendor docs
    # DeepSeek
    "deepseek-r1-distill-qwen-32b": "2024-09",  # based on Qwen2.5-32B base; verify
    "deepseek-r1": "2024-09",            # prefix-match fallback for ollama "deepseek-r1:32b" form
}


def _registry_match(model_id: str) -> Optional[str]:
    """Exact then bidirectional-prefix match against TRAINING_CUTOFFS.

    No env/config layer — pure table lookup. Prefix matching handles versioned
    IDs ('claude-opus-4-7-20260115' -> 'claude-opus-4-7') and the Ollama tag
    form ('deepseek-r1:32b' -> 'deepseek-r1').
    """
    if model_id in TRAINING_CUTOFFS:
        return TRAINING_CUTOFFS[model_id]
    for known_id in TRAINING_CUTOFFS:
        if model_id.startswith(known_id):
            return TRAINING_CUTOFFS[known_id]
    for known_id in TRAINING_CUTOFFS:
        if known_id.startswith(model_id):
            return TRAINING_CUTOFFS[known_id]
    return None


def lookup_cutoff(model_id: str) -> Optional[str]:
    """Return the cutoff string for a model ID, or None if unknown.

    Resolution order:
    1. ASOF_TRAINING_CUTOFF env var (user override, takes precedence)
    2. Exact match in TRAINING_CUTOFFS
    3. Prefix match (handles versioned IDs like 'claude-opus-4-7-20260115')
    4. None

    Preserved for callers that want a bare cutoff string (query.py, the MCP
    server). resolve_cutoff() is the richer entry point used by session_init.
    """
    override = os.environ.get("ASOF_TRAINING_CUTOFF")
    if override and override.strip():
        return override.strip()
    return _registry_match(model_id)


def cutoff_to_date(cutoff: str) -> date:
    """Convert a 'YYYY-MM' or 'YYYY-MM-DD' cutoff string to a date.

    Assumes first-of-month if only year-month given.
    """
    parts = cutoff.split("-")
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1)
    if len(parts) == 3:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError(f"cutoff must be YYYY-MM or YYYY-MM-DD, got {cutoff!r}")


def gap_to_now(cutoff: str, *, now: Optional[date] = None) -> dict:
    """Compute the gap from a training cutoff to now.

    Returns a dict with:
    - days: total days elapsed
    - months: approximate months (days / 30)
    - human: a human-readable phrase ('4 months ago', '2 years 3 months ago')
    """
    if now is None:
        now = datetime.now(timezone.utc).date()
    cutoff_d = cutoff_to_date(cutoff)
    days = (now - cutoff_d).days
    if days < 0:
        return {"days": days, "months": 0, "human": "(cutoff is in the future)"}
    months = days // 30
    years = months // 12
    rem_months = months % 12

    if years >= 1:
        if rem_months > 0:
            human = f"{years} year{'s' if years > 1 else ''} {rem_months} month{'s' if rem_months > 1 else ''} ago"
        else:
            human = f"{years} year{'s' if years > 1 else ''} ago"
    elif months >= 1:
        human = f"~{months} month{'s' if months > 1 else ''} ago"
    elif days >= 7:
        weeks = days // 7
        human = f"~{weeks} week{'s' if weeks > 1 else ''} ago"
    else:
        human = f"{days} day{'s' if days != 1 else ''} ago"

    return {"days": days, "months": months, "human": human}


# ----------------------------------------------------------------------------
# Cutoff resolution with provenance (env -> config -> registry -> ollama scan)
# ----------------------------------------------------------------------------

def _config_cutoff(model_id: str) -> Optional[str]:
    """Look up a per-model cutoff in ~/.asof/config.json's `cutoffs` map.

    This is the documented, operator-owned accuracy path for models AsOf does
    not recognize. Returns None on any miss or malformed config (never raises).
    """
    try:
        cfg_path = Path.home() / ".asof" / "config.json"
        if not cfg_path.is_file():
            return None
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        mapping = cfg.get("cutoffs", {}) or {}
        val = mapping.get(model_id)
        if isinstance(val, str) and val.strip():
            return val.strip()
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        pass
    return None


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

# Anchored cutoff patterns. Each binds a date *to* an explicit knowledge/
# training-cutoff phrase, with the date as the capture group immediately after
# the anchor. Deliberately strict: modelfiles also carry Apache-license
# boilerplate ("as of the date such litigation is filed", "Copyright [yyyy]")
# and release/license dates ("Last modified: February 21, 2024" in gemma2)
# that must NOT be read as a knowledge cutoff. Verified 2026-05-29 against the
# real `ollama show --modelfile` output for mistral-small (hits 2023-10),
# llama3.1 (hits 2023-12), gemma2 and qwen2.5 (both correctly miss).
_DATE = r"([A-Za-z]+\.?\s+\d{4}|\d{4}-\d{2}(?:-\d{2})?)"
_OLLAMA_CUTOFF_PATTERNS = [
    re.compile(r"cutting\s+knowledge\s+date\s*[:\-]?\s*" + _DATE, re.IGNORECASE),
    re.compile(r"knowledge\s+(?:base\s+)?(?:was\s+)?(?:last\s+)?updated\s+"
               r"(?:on\s+)?[:\-]?\s*" + _DATE, re.IGNORECASE),
    re.compile(r"knowledge\s+cutoff\s*[:\-]?\s*" + _DATE, re.IGNORECASE),
    re.compile(r"training\s+(?:data\s+)?cutoff\s*[:\-]?\s*" + _DATE, re.IGNORECASE),
]


def _normalize_scanned_date(raw: str) -> Optional[str]:
    """Normalize a scanned date ('December 2023', '2023-10-01') to 'YYYY-MM'."""
    raw = raw.strip()
    iso = re.match(r"(\d{4})-(\d{2})", raw)
    if iso:
        mo = int(iso.group(2))
        if 1 <= mo <= 12:
            return f"{int(iso.group(1)):04d}-{mo:02d}"
    name = re.match(r"([A-Za-z]+)\.?\s+(\d{4})", raw)
    if name:
        mo = _MONTHS.get(name.group(1)[:3].lower())
        if mo:
            return f"{int(name.group(2)):04d}-{mo:02d}"
    return None


def _default_ollama_modelfile(model_id: str) -> Optional[str]:
    """Return `ollama show <model> --modelfile` text, or None.

    Static metadata only — this does NOT run the model. Guarded so a box
    without Ollama (the common Claude-Code case) pays no cost and never errors.
    """
    if not shutil.which("ollama"):
        return None
    try:
        proc = subprocess.run(
            ["ollama", "show", model_id, "--modelfile"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            return proc.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _scan_ollama_cutoff(model_id: str, *, runner=None) -> Optional[str]:
    """Scan an Ollama modelfile for an anchored knowledge-cutoff date.

    `runner(model_id) -> Optional[str]` is injected for testing; defaults to
    the real `ollama show` call. Returns 'YYYY-MM' or None.
    """
    text = (runner or _default_ollama_modelfile)(model_id)
    if not text:
        return None
    for pat in _OLLAMA_CUTOFF_PATTERNS:
        m = pat.search(text)
        if m:
            norm = _normalize_scanned_date(m.group(1))
            if norm:
                return norm
    return None


def resolve_cutoff(model_id: Optional[str], *, allow_ollama_scan: bool = True,
                   ollama_runner=None) -> dict:
    """Resolve a training cutoff with provenance. Never raises.

    Layers, highest precedence first:
      1. ASOF_TRAINING_CUTOFF env var    -> source "env"
      2. ~/.asof/config.json cutoffs map -> source "config"   (operator path)
      3. TRAINING_CUTOFFS registry       -> source "registry"
      4. anchored Ollama modelfile scan  -> source "ollama-metadata"
      5. nothing                         -> source "none"  (conservative)

    The model is never asked its own cutoff. Returns
    {"cutoff": Optional[str], "source": str}.
    """
    env = os.environ.get("ASOF_TRAINING_CUTOFF")
    if env and env.strip():
        return {"cutoff": env.strip(), "source": "env"}

    if not model_id:
        return {"cutoff": None, "source": "none"}

    cfg = _config_cutoff(model_id)
    if cfg:
        return {"cutoff": cfg, "source": "config"}

    reg = _registry_match(model_id)
    if reg:
        return {"cutoff": reg, "source": "registry"}

    if allow_ollama_scan:
        scanned = _scan_ollama_cutoff(model_id, runner=ollama_runner)
        if scanned:
            return {"cutoff": scanned, "source": "ollama-metadata"}

    return {"cutoff": None, "source": "none"}


def build_cutoff_posture(model_id: Optional[str], *, now: Optional[date] = None,
                         allow_ollama_scan: bool = True, ollama_runner=None) -> dict:
    """Resolve a cutoff and pre-compute the gap into a render-ready posture.

    Always returns a dict the renderer can turn into a "## Training cutoff"
    block — known (with a pre-computed human gap) or unknown (conservative,
    naming the operator's config path). A malformed env/config cutoff degrades
    to unknown rather than raising.

    Keys: known(bool), cutoff_str(Optional[str]), human(Optional[str]),
    source(str), model_id(Optional[str]).
    """
    res = resolve_cutoff(model_id, allow_ollama_scan=allow_ollama_scan,
                         ollama_runner=ollama_runner)
    posture: dict = {
        "known": False,
        "cutoff_str": None,
        "human": None,
        "source": res["source"],
        "model_id": model_id,
    }
    cutoff = res["cutoff"]
    if cutoff:
        try:
            gap = gap_to_now(cutoff, now=now)
            posture.update({
                "known": True,
                "cutoff_str": cutoff,
                "human": gap["human"],
                "days": gap["days"],
                "months": gap["months"],
            })
        except (ValueError, TypeError):
            # Malformed operator-supplied cutoff: degrade to unknown, flag it.
            posture["source"] = f"invalid:{res['source']}"
    return posture
