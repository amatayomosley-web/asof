"""AsOf CLI — install, config, check, query.

Entry point registered in pyproject.toml as `asof`. Routes subcommands
to the right adapter or core module.

Usage:
    asof install [--adapter claude_code|antigravity|generic]
    asof check
    asof config [get|set|add-domain|remove-domain] [args]
    asof query <target>
    asof --version
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


CONFIG_DIR = Path.home() / ".asof"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _detect_substrate() -> str | None:
    """Heuristic substrate detection.

    Returns "claude_code" if ~/.claude/settings.json exists,
    "antigravity" if ~/.gemini/config/hooks.json exists, else None.
    """
    if (Path.home() / ".claude" / "settings.json").is_file() or (Path.home() / ".claude").is_dir():
        return "claude_code"
    if (Path.home() / ".gemini" / "config" / "hooks.json").is_file() or (Path.home() / ".gemini").is_dir():
        return "antigravity"
    return None


def _load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def cmd_install(args: argparse.Namespace) -> int:
    adapter = args.adapter
    if adapter is None:
        adapter = _detect_substrate()
    if adapter is None:
        print("Could not auto-detect substrate. Pass --adapter explicitly:")
        print("  asof install --adapter claude_code")
        print("  asof install --adapter antigravity")
        print("  asof install --adapter generic")
        return 2

    print(f"Installing AsOf adapter: {adapter}")
    print()

    # Run the adapter's install module
    try:
        result = subprocess.run(
            [sys.executable, "-m", f"adapters.{adapter}.install"],
            check=False,
        )
        return result.returncode
    except (OSError, FileNotFoundError) as e:
        print(f"FAIL: could not run adapter installer: {e}")
        return 1


def cmd_check(args: argparse.Namespace) -> int:
    """Verify AsOf installation health."""
    print("AsOf installation check:")
    print()

    try:
        import asof_core
        print(f"  asof_core importable (v{asof_core.__version__})")
    except ImportError as e:
        print(f"  FAIL: asof_core not importable: {e}")
        return 1

    substrate = _detect_substrate()
    print(f"  Detected substrate: {substrate or 'none'}")

    config = _load_config()
    if config:
        print(f"  Config loaded from {CONFIG_PATH}")
        if config.get("patterns", {}).get("domains"):
            print(f"  Active domains: {', '.join(config['patterns']['domains'])}")
    else:
        print(f"  No config at {CONFIG_PATH} (defaults will be used)")

    # Try a session-init dry run
    try:
        from asof_core.hooks import session_init
        out = session_init(model_id="claude-opus-4-7", session_id="check")
        if "AsOf v" in out:
            print(f"  Session-init dry-run: OK")
        else:
            print(f"  WARN: session-init produced unexpected output")
    except Exception as e:
        print(f"  FAIL: session-init dry-run errored: {e}")
        return 1

    print()
    print("Check complete.")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """View or modify AsOf config."""
    config = _load_config()

    if args.action == "show" or args.action is None:
        print(json.dumps(config, indent=2))
        return 0

    if args.action == "add-domain":
        if not args.value:
            print("Usage: asof config add-domain <name>")
            return 2
        patterns = config.setdefault("patterns", {})
        domains = patterns.setdefault("domains", [])
        if args.value not in domains:
            domains.append(args.value)
            _save_config(config)
            print(f"Added domain: {args.value}")
        else:
            print(f"Domain already active: {args.value}")
        return 0

    if args.action == "remove-domain":
        if not args.value:
            print("Usage: asof config remove-domain <name>")
            return 2
        domains = config.get("patterns", {}).get("domains", [])
        if args.value in domains:
            domains.remove(args.value)
            _save_config(config)
            print(f"Removed domain: {args.value}")
        else:
            print(f"Domain not currently active: {args.value}")
        return 0

    if args.action == "set":
        if not args.value:
            print("Usage: asof config set <key>=<value>")
            return 2
        if "=" not in args.value:
            print("Format: key=value (e.g., mode=strict)")
            return 2
        key, _, value = args.value.partition("=")
        config[key] = value
        _save_config(config)
        print(f"Set {key} = {value}")
        return 0

    print(f"Unknown config action: {args.action}")
    return 2


def cmd_query(args: argparse.Namespace) -> int:
    """Run the asof_query oracle on a target."""
    try:
        from asof_core.query import query
    except ImportError as e:
        print(f"FAIL: {e}")
        return 1

    result = query(args.target)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Print version info."""
    try:
        import asof_core
        print(f"asof {asof_core.__version__}")
        print(f"schema {asof_core.SCHEMA_VERSION}")
        print(f"prose-min {asof_core.MIN_PROSE_VERSION}")
    except ImportError as e:
        print(f"FAIL: {e}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="asof",
        description="AsOf — temporal awareness for tool-using LLMs",
    )
    subparsers = parser.add_subparsers(dest="command")

    p_install = subparsers.add_parser("install", help="install AsOf adapter")
    p_install.add_argument("--adapter", choices=["claude_code", "antigravity", "generic"], default=None)
    p_install.set_defaults(func=cmd_install)

    p_check = subparsers.add_parser("check", help="verify installation")
    p_check.set_defaults(func=cmd_check)

    p_config = subparsers.add_parser("config", help="view or modify config")
    p_config.add_argument("action", nargs="?", choices=["show", "add-domain", "remove-domain", "set"])
    p_config.add_argument("value", nargs="?")
    p_config.set_defaults(func=cmd_config)

    p_query = subparsers.add_parser("query", help="query the oracle for a target")
    p_query.add_argument("target", help="file path, URL, timestamp, model ID, or text")
    p_query.set_defaults(func=cmd_query)

    p_version = subparsers.add_parser("version", help="print version info")
    p_version.set_defaults(func=cmd_version)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
