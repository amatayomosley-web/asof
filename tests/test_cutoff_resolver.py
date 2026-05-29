"""Tests for the layered cutoff resolver (cutoffs.resolve_cutoff /
build_cutoff_posture) and the always-emit cutoff posture in session_init.

Resolution layers, highest precedence first:
    env -> ~/.asof/config.json cutoffs map -> registry -> Ollama metadata scan
    -> conservative "unknown" posture.

The Ollama scan is exercised with an injected runner over the REAL
`ollama show --modelfile` text (verified 2026-05-29), so the trap cases —
Apache-license "as of the date ...", "Copyright [yyyy]", gemma2's "Last
modified: February 21, 2024", llama's "Version Release Date: July 23, 2024" —
are the actual strings the anchored patterns must reject. No live Ollama
needed: an autouse fixture neutralizes the real subprocess call.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from asof_core import cutoffs
from asof_core.hooks.session_init import session_init
from asof_core.output import _format_cutoff_section


# --- Real modelfile snippets (the trap strings are intentional) -------------

MISTRAL_MODELFILE = (
    "SYSTEM You are Mistral Small 3, a Large Language Model (LLM) created by "
    "Mistral AI. Your knowledge base was last updated on 2023-10-01. When "
    "you're not sure about some information, you say so.\n"
    '      "Licensor" shall mean the copyright owner or entity authorized by\n'
    "      as of the date such litigation is filed.\n"
    "   Copyright [yyyy] [name of copyright owner]\n"
)
LLAMA_MODELFILE = (
    "Cutting Knowledge Date: December 2023\n"
    "Llama 3.1 Version Release Date: July 23, 2024\n"
    "   licensed under the Llama 3.1 Community License, Copyright (c) Meta\n"
    "    5. Self-harm or harm to others, including suicide, cutting, and "
    "eating disorders\n"
)
GEMMA2_MODELFILE = (
    "Last modified: February 21, 2024\n"
    '(c) "Gemma" means the set of machine learning language models, trained '
    "model weights and parameters identified at ai.google.dev/gemma.\n"
    "4.1 Updates\n"
    "Google may update Gemma from time to time, and you must make reasonable "
    "efforts to use the latest version of Gemma.\n"
    "This Agreement states all the terms ... as of the date of acceptance.\n"
)
QWEN_MODELFILE = (
    '      "Licensor" shall mean the copyright owner or entity authorized by\n'
    "      as of the date such litigation is filed.\n"
    "   Copyright 2024 Alibaba Cloud\n"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No global env override; a clean fake home so the real ~/.asof config
    never leaks; and the real `ollama show` subprocess neutralized so a scan
    only ever sees what a test injects via ollama_runner=."""
    monkeypatch.delenv("ASOF_TRAINING_CUTOFF", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cutoffs, "_default_ollama_modelfile", lambda model_id: None)
    return tmp_path


def _write_config(home: Path, data: dict) -> None:
    d = home / ".asof"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(data), encoding="utf-8")


# --- _normalize_scanned_date ------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("December 2023", "2023-12"),
    ("Dec 2023", "2023-12"),
    ("2023-10-01", "2023-10"),
    ("2023-10", "2023-10"),
    ("February 21, 2024", None),         # 'Month DD, YYYY' (license form) NOT parsed
    ("garbage", None),
    ("2023-13", None),                  # invalid month, no name fallback
])
def test_normalize_scanned_date(raw, expected):
    assert cutoffs._normalize_scanned_date(raw) == expected


# --- _scan_ollama_cutoff: positives bind to the anchor, traps are rejected --

def test_scan_hits_mistral_knowledge_base_line():
    assert cutoffs._scan_ollama_cutoff("m", runner=lambda _: MISTRAL_MODELFILE) == "2023-10"


def test_scan_hits_llama_cutoff_not_release_date():
    # Must read 'Cutting Knowledge Date: December 2023', NOT the nearby
    # 'Version Release Date: July 23, 2024'.
    assert cutoffs._scan_ollama_cutoff("l", runner=lambda _: LLAMA_MODELFILE) == "2023-12"


def test_scan_rejects_gemma2_license_modified_date():
    # 'Last modified: February 21, 2024' is a license date, not a cutoff.
    assert cutoffs._scan_ollama_cutoff("g", runner=lambda _: GEMMA2_MODELFILE) is None


def test_scan_rejects_qwen_copyright_and_apache_boilerplate():
    # Only 'Copyright 2024' + 'as of the date such litigation is filed'.
    assert cutoffs._scan_ollama_cutoff("q", runner=lambda _: QWEN_MODELFILE) is None


def test_scan_empty_or_missing_modelfile_is_none():
    assert cutoffs._scan_ollama_cutoff("x", runner=lambda _: "") is None
    assert cutoffs._scan_ollama_cutoff("x", runner=lambda _: None) is None


# --- resolve_cutoff: layer precedence ---------------------------------------

def test_env_override_wins_even_for_known_model(monkeypatch):
    monkeypatch.setenv("ASOF_TRAINING_CUTOFF", "2099-01")
    res = cutoffs.resolve_cutoff("claude-opus-4-8")
    assert res == {"cutoff": "2099-01", "source": "env"}


def test_config_map_overrides_registry(_isolated):
    _write_config(_isolated, {"cutoffs": {"claude-opus-4-8": "2020-01"}})
    res = cutoffs.resolve_cutoff("claude-opus-4-8", allow_ollama_scan=False)
    assert res == {"cutoff": "2020-01", "source": "config"}


def test_config_map_resolves_unknown_local_model(_isolated):
    _write_config(_isolated, {"cutoffs": {"my-local:latest": "2024-06"}})
    res = cutoffs.resolve_cutoff("my-local:latest", allow_ollama_scan=False)
    assert res == {"cutoff": "2024-06", "source": "config"}


def test_registry_exact_and_prefix():
    assert cutoffs.resolve_cutoff("claude-opus-4-8", allow_ollama_scan=False) == {
        "cutoff": "2026-01", "source": "registry"}
    # Ollama tag form prefix-matches the bare registry key.
    assert cutoffs.resolve_cutoff("deepseek-r1:32b", allow_ollama_scan=False) == {
        "cutoff": "2024-09", "source": "registry"}


def test_scan_layer_only_for_unregistered_model():
    res = cutoffs.resolve_cutoff("totally-unknown:latest",
                                 ollama_runner=lambda _: MISTRAL_MODELFILE)
    assert res == {"cutoff": "2023-10", "source": "ollama-metadata"}


def test_unknown_falls_through_to_none():
    res = cutoffs.resolve_cutoff("totally-unknown:latest", allow_ollama_scan=False)
    assert res == {"cutoff": None, "source": "none"}


def test_unknown_with_trap_modelfile_stays_none():
    # Scan runs, sees only license/copyright dates, refuses them.
    res = cutoffs.resolve_cutoff("totally-unknown:latest",
                                 ollama_runner=lambda _: GEMMA2_MODELFILE)
    assert res == {"cutoff": None, "source": "none"}


def test_no_model_id_is_none():
    assert cutoffs.resolve_cutoff(None) == {"cutoff": None, "source": "none"}


# --- build_cutoff_posture: pre-computed gap + graceful degradation ----------

def test_posture_known_precomputes_gap():
    p = cutoffs.build_cutoff_posture("claude-opus-4-8", now=date(2026, 5, 29),
                                     allow_ollama_scan=False)
    assert p["known"] is True
    assert p["cutoff_str"] == "2026-01"
    assert p["human"] == "~4 months ago"
    assert p["source"] == "registry"
    assert p["model_id"] == "claude-opus-4-8"


def test_posture_unknown_carries_model_id():
    p = cutoffs.build_cutoff_posture("zzz-local:9b", now=date(2026, 5, 29),
                                     allow_ollama_scan=False)
    assert p["known"] is False
    assert p["cutoff_str"] is None
    assert p["model_id"] == "zzz-local:9b"
    assert p["source"] == "none"


def test_posture_malformed_config_cutoff_degrades(_isolated):
    _write_config(_isolated, {"cutoffs": {"weird:1b": "not-a-date"}})
    p = cutoffs.build_cutoff_posture("weird:1b", now=date(2026, 5, 29),
                                     allow_ollama_scan=False)
    assert p["known"] is False
    assert p["cutoff_str"] is None
    assert p["source"] == "invalid:config"   # flagged, not crashed


# --- _format_cutoff_section: known vs conservative-unknown rendering --------

def test_render_known_section():
    lines = "\n".join(_format_cutoff_section(
        {"cutoff_str": "2026-01", "human": "~4 months ago"}))
    assert "## Training cutoff" in lines
    assert "Cutoff: 2026-01 (~4 months ago)" in lines


def test_render_unknown_section_names_model_and_config_path():
    lines = "\n".join(_format_cutoff_section(
        {"cutoff_str": None, "model_id": "foo:1b"}))
    assert "## Training cutoff" in lines
    assert "unknown for 'foo:1b'" in lines
    assert "~/.asof/config.json" in lines
    assert '"cutoffs"' in lines
    assert '"foo:1b"' in lines


def test_render_unknown_section_without_model_id_uses_placeholder():
    lines = "\n".join(_format_cutoff_section({"cutoff_str": None}))
    assert "<model-id>" in lines


# --- session_init ALWAYS emits a cutoff posture (the requirement) -----------

def test_session_init_known_model_emits_precise_cutoff(tmp_path):
    out = session_init(model_id="claude-opus-4-7", session_id="s",
                       log_dir=tmp_path / "log")
    assert "## Training cutoff" in out
    assert "2026-01" in out


def test_session_init_unknown_model_emits_conservative_posture(tmp_path):
    out = session_init(model_id="zzz-unknown:9b", session_id="s",
                       log_dir=tmp_path / "log")
    assert "## Training cutoff" in out
    assert "unknown" in out.lower()
    assert "zzz-unknown:9b" in out
    assert "~/.asof/config.json" in out


def test_session_init_no_model_id_still_emits_posture(tmp_path):
    out = session_init(model_id=None, session_id="s", log_dir=tmp_path / "log")
    assert "## Training cutoff" in out
    assert "<model-id>" in out
