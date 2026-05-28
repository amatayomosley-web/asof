# AsOf — Claude Code install guide

## Quick install

```bash
pip install asof
asof install
```

This auto-detects Claude Code at `~/.claude/`, copies `SKILL.md`, patches `settings.json`. Restart Claude Code to activate.

## Manual install

If you prefer to wire it up yourself:

### 1. Install the Python package

```bash
pip install asof
```

Verify: `python -c "import asof_core; print(asof_core.__version__)"`

### 2. Copy the SKILL.md

```bash
mkdir -p ~/.claude/skills/asof
cp $(python -c "import asof_core, os; print(os.path.dirname(asof_core.__file__))")/../adapters/claude_code/SKILL.md ~/.claude/skills/asof/SKILL.md
```

### 3. Patch settings.json

Open `~/.claude/settings.json` and merge these into the `hooks` block:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python -m asof_core.adapters.claude_code.session_init",
            "timeout": 5000
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python -m asof_core.adapters.claude_code.watch",
            "timeout": 5000
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python -m asof_core.adapters.claude_code.post_tool",
            "timeout": 2000
          }
        ]
      }
    ]
  }
}
```

If you already have hooks for these events, append the AsOf entries to the existing arrays — Claude Code runs all hooks in an event's array sequentially.

### 4. Verify

```bash
asof check
```

Should print:
```
asof_core importable (v0.1.0)
Detected substrate: claude_code
Session-init dry-run: OK
```

### 5. Restart Claude Code

The hooks load at session start. Existing sessions don't pick up newly-installed hooks until restarted.

## Testing the install

Open a new Claude Code session. Read a file. Then in another terminal, edit that file. Send a new prompt. The watch hook should emit:

```
=== AsOf v0.1.0 ===
Now: ...

## File freshness (this session)
  STALE  /path/to/file  mtime moved Xm after read, no matching self-write
```

If the STALE alert appears: install is working.

## Uninstall

```bash
# Remove SKILL.md
rm -rf ~/.claude/skills/asof

# Remove hook entries from settings.json (manual edit, or)
asof install --uninstall  # V2
```

Then `pip uninstall asof`.

## Troubleshooting

### No STALE alert when I edited a file externally

- Verify `~/.asof/tool_log/<session_id>.jsonl` has entries (the PostToolUse hook is logging)
- Verify `python -m asof_core.adapters.claude_code.post_tool` runs without errors when given a tool event on stdin
- Check `~/.claude/settings.json` has the PostToolUse hook entry

### Hook produces no output even for time-sensitive prompts

- The adaptive renderer stays silent when no section has actionable content
- Try `ASOF_MODE=strict` for more verbose output during debugging
- Check `~/.asof/config.json` for unexpected pattern config

### "INCOMPATIBLE" notice in AsOf output

The hook version and SKILL.md version don't match per the schema-version contract. Run:

```bash
pip install --upgrade asof
asof install
```

This re-syncs SKILL.md to the matching schema version.

### Hook is slow (visible delay before responses)

- Run `asof check` — confirms hook scripts can import asof_core quickly
- Check tool_log size: very long sessions accumulate (V2 will rotate)
- Set `ASOF_MODE=silent` to disable per-turn watch output if needed temporarily

### Config not loading

`asof config show` should print the current config. If empty, `~/.asof/config.json` may be malformed JSON. Validate with `python -m json.tool ~/.asof/config.json`.
