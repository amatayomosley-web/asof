#!/usr/bin/env python
"""install.py

Installer script for the AsOf temporal-awareness skill Antigravity adapter.
"""
from __future__ import annotations

import os
import sys
import json
import shutil
from pathlib import Path

def main() -> int:
    print("[AsOf Install] Starting installation of Antigravity adapter...")
    
    # 1. Define paths
    home_dir = Path.home()
    gemini_config_dir = home_dir / ".gemini" / "config"
    hooks_file = gemini_config_dir / "hooks.json"
    sidecars_dir = gemini_config_dir / "sidecars" / "asof"
    plugins_dir = gemini_config_dir / "plugins" / "asof"
    
    script_dir = Path(__file__).resolve().parent
    orchestrator_source = script_dir / "asof_antigravity_orchestrator.py"
    snippet_source = script_dir / "hooks_snippet.json"
    skill_source = script_dir / "SKILL.md"

    # Verify source files exist
    for f in (orchestrator_source, snippet_source, skill_source):
        if not f.exists():
            print(f"[AsOf Install] ERROR: Source file {f.name} missing in installation package.")
            return 1

    # 2. Create target directories
    sidecars_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # 3. Copy files to sidecar and plugin directory
    orchestrator_target = sidecars_dir / "asof_antigravity_orchestrator.py"
    shutil.copy2(orchestrator_source, orchestrator_target)
    os.chmod(orchestrator_target, 0o755)
    print(f"[AsOf Install] Copied orchestrator script to {orchestrator_target}")

    skill_target = plugins_dir / "SKILL.md"
    shutil.copy2(skill_source, skill_target)
    print(f"[AsOf Install] Copied SKILL.md to {skill_target}")

    # 4. Patch hooks.json
    hooks_data = {}
    if hooks_file.exists():
        try:
            hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
            print(f"[AsOf Install] Found existing hooks configuration at {hooks_file}")
        except Exception as e:
            print(f"[AsOf Install] WARNING: Failed to read existing hooks.json ({e}). Re-initializing.")
            hooks_data = {}

    # Read and process hook snippet template
    try:
        snippet_text = snippet_source.read_text(encoding="utf-8")
        # Fully resolve paths with forward slashes for Windows compatibility
        resolved_orch_path = str(orchestrator_target.resolve()).replace("\\", "/")
        snippet_text = snippet_text.replace("{{ORCHESTRATOR_PATH}}", resolved_orch_path)
        snippet_json = json.loads(snippet_text)
    except Exception as e:
        print(f"[AsOf Install] ERROR: Failed to process hook snippet ({e}).")
        return 1

    # Merge hook config under the "current" agent block
    if "current" not in hooks_data:
        hooks_data["current"] = {}
    if "PreInvocation" not in hooks_data["current"]:
        hooks_data["current"]["PreInvocation"] = []

    # Check if hook already exists to make registration idempotent
    existing_hooks = hooks_data["current"]["PreInvocation"]
    new_hook = snippet_json["current"]["PreInvocation"][0]
    
    hook_found = False
    for hook in existing_hooks:
        if hook.get("command") == new_hook.get("command"):
            hook_found = True
            break
            
    if not hook_found:
        existing_hooks.append(new_hook)
        print("[AsOf Install] Appended PreInvocation orchestrator hook.")
    else:
        print("[AsOf Install] PreInvocation hook is already registered (idempotency check passed).")

    # Write back hooks.json
    try:
        hooks_file.parent.mkdir(parents=True, exist_ok=True)
        hooks_file.write_text(json.dumps(hooks_data, indent=2), encoding="utf-8")
        print(f"[AsOf Install] Successfully updated {hooks_file}")
    except Exception as e:
        print(f"[AsOf Install] ERROR: Failed to write hooks.json ({e}).")
        return 1

    print("[AsOf Install] Installation completed successfully.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
