"""Claude Code adapter installer.

Idempotently:
1. Creates ~/.claude/skills/asof/ and copies SKILL.md there
2. Patches ~/.claude/settings.json to add AsOf hook entries
3. Verifies the install via a dry-run hook fire

Run via: asof install --adapter claude_code (recommended)
Or directly: python -m adapters.claude_code.install
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


CLAUDE_HOME = Path.home() / ".claude"
SKILL_DIR = CLAUDE_HOME / "skills" / "asof"
SETTINGS_PATH = CLAUDE_HOME / "settings.json"

ADAPTER_DIR = Path(__file__).resolve().parent


def install_skill_file() -> None:
    """Copy SKILL.md from the adapter to ~/.claude/skills/asof/."""
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    src = ADAPTER_DIR / "SKILL.md"
    dst = SKILL_DIR / "SKILL.md"
    shutil.copyfile(src, dst)
    print(f"  SKILL.md installed at {dst}")


def patch_settings() -> bool:
    """Add AsOf hooks to settings.json. Idempotent.

    Returns True if changes were made, False if already present.
    """
    snippet_path = ADAPTER_DIR / "hooks_snippet.json"
    with snippet_path.open(encoding="utf-8") as f:
        snippet = json.load(f)
    asof_hooks = snippet["hooks"]

    # Load existing settings
    if SETTINGS_PATH.is_file():
        with SETTINGS_PATH.open(encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {}

    if "hooks" not in settings:
        settings["hooks"] = {}

    changed = False
    for event_name, event_hooks in asof_hooks.items():
        existing = settings["hooks"].get(event_name, [])

        # Check if our hook is already there (by command match)
        for asof_block in event_hooks:
            for asof_cmd in asof_block.get("hooks", []):
                cmd_str = asof_cmd.get("command", "")
                already_present = False
                for existing_block in existing:
                    for existing_cmd in existing_block.get("hooks", []):
                        if existing_cmd.get("command") == cmd_str:
                            already_present = True
                            break
                    if already_present:
                        break
                if not already_present:
                    # Append our block
                    existing.append({"hooks": [asof_cmd]})
                    changed = True

        settings["hooks"][event_name] = existing

    if changed:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_PATH.open("w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        print(f"  settings.json patched at {SETTINGS_PATH}")
    else:
        print(f"  settings.json already contains AsOf hooks; no change")

    return changed


def verify() -> bool:
    """Quick verification: can we import asof_core, do the hook scripts
    exist on disk, does settings.json have the entries?"""
    print("\nVerifying installation:")
    ok = True

    try:
        import asof_core
        print(f"  asof_core importable (v{asof_core.__version__})")
    except ImportError as e:
        print(f"  FAIL: asof_core not importable: {e}")
        ok = False

    for hook_script in ["session_init.py", "watch.py", "post_tool.py"]:
        p = ADAPTER_DIR / hook_script
        if p.is_file():
            print(f"  {hook_script} present")
        else:
            print(f"  FAIL: {hook_script} missing at {p}")
            ok = False

    if SETTINGS_PATH.is_file():
        with SETTINGS_PATH.open(encoding="utf-8") as f:
            settings = json.load(f)
        hooks = settings.get("hooks", {})
        for event in ["SessionStart", "UserPromptSubmit", "PostToolUse"]:
            entries = hooks.get(event, [])
            has_asof = any(
                "asof" in (cmd.get("command", "") or "")
                for block in entries
                for cmd in block.get("hooks", [])
            )
            if has_asof:
                print(f"  {event} hook wired")
            else:
                print(f"  WARN: {event} hook not found in settings.json")
                ok = False
    else:
        print(f"  FAIL: settings.json not found at {SETTINGS_PATH}")
        ok = False

    return ok


def main() -> int:
    print("Installing AsOf for Claude Code...")
    print()

    install_skill_file()
    patch_settings()
    ok = verify()

    print()
    if ok:
        print("Install complete. Restart Claude Code to activate.")
        return 0
    else:
        print("Install completed with warnings. See output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
