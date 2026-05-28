"""Tests for the Antigravity installer — it must never leave a dangling hook
(a PreInvocation hook pointing at a missing file is fatal in Antigravity) and
must place the orchestrator in a hooks dir, not a sidecar dir."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

# Load adapters/antigravity/install.py by path (avoid colliding with the
# claude_code adapter's install.py in sys.modules).
_INSTALL = Path(__file__).resolve().parents[1] / "adapters" / "antigravity" / "install.py"
_spec = importlib.util.spec_from_file_location("asof_antigravity_install", _INSTALL)
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)


def _asof_command(hooks_json: dict) -> str:
    cmds = [h["command"] for h in hooks_json["current"]["PreInvocation"]]
    return next(c for c in cmds if "asof_antigravity_orchestrator.py" in c)


def test_install_places_orchestrator_in_hooks_and_registers_existing_file(tmp_path):
    cfg = tmp_path / ".gemini" / "config"
    assert install.main(config_dir=cfg) == 0

    orch = cfg / "hooks" / "asof" / "asof_antigravity_orchestrator.py"
    assert orch.is_file() and orch.stat().st_size > 0

    # Never use a sidecar dir (it makes Antigravity's sidecar_manager warn).
    assert not (cfg / "sidecars" / "asof").exists()

    # The registered hook command must point at a file that actually exists —
    # no dangling hook (the failure mode that bricked Current).
    hooks = json.loads((cfg / "hooks.json").read_text(encoding="utf-8"))
    cmd = _asof_command(hooks)
    path_str = cmd.replace("python ", "").strip()
    assert Path(path_str).is_file(), f"hook points at a missing file: {path_str}"


def test_install_is_idempotent(tmp_path):
    cfg = tmp_path / ".gemini" / "config"
    assert install.main(config_dir=cfg) == 0
    assert install.main(config_dir=cfg) == 0
    hooks = json.loads((cfg / "hooks.json").read_text(encoding="utf-8"))
    asof = [h for h in hooks["current"]["PreInvocation"]
            if "asof_antigravity_orchestrator" in h["command"]]
    assert len(asof) == 1, "re-running the installer must not duplicate the hook"


def test_install_preserves_other_agents_hooks(tmp_path):
    cfg = tmp_path / ".gemini" / "config"
    cfg.mkdir(parents=True)
    (cfg / "hooks.json").write_text(
        json.dumps({"other": {"PreInvocation": [{"type": "command", "command": "echo hi"}]}}),
        encoding="utf-8",
    )
    assert install.main(config_dir=cfg) == 0
    hooks = json.loads((cfg / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["other"]["PreInvocation"][0]["command"] == "echo hi"
    assert "current" in hooks
