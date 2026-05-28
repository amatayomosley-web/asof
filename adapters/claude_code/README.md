# AsOf — Claude Code adapter

Temporal awareness for Claude Code sessions.

## Install

```bash
pip install asof
asof install
```

The installer auto-detects Claude Code at `~/.claude/` and:
1. Copies `SKILL.md` to `~/.claude/skills/asof/`
2. Patches `~/.claude/settings.json` to wire three hooks
3. Verifies the install

Restart Claude Code after install to activate.

## What gets wired

Three hook events:
- **`SessionStart`** → emits the directive block (training cutoff, current time, teaching prose pointer)
- **`UserPromptSubmit`** → emits the adaptive watch block (only when there's an actionable signal)
- **`PostToolUse`** → silently logs tool calls for the freshness mechanism

## Config

`~/.asof/config.json`:

```json
{
  "patterns": {
    "high_confidence": true,
    "medium_confidence": true,
    "domains": ["finance", "travel"]
  },
  "mode": "normal",
  "file_annotation": false
}
```

Or environment variables:
- `ASOF_DOMAINS=finance,travel`
- `ASOF_MODE=silent|normal|strict`
- `ASOF_FILE_ANNOTATION=on|off`
- `ASOF_TRAINING_CUTOFF=2026-01` (override the model→cutoff lookup)

## Uninstall

Remove the entries from `~/.claude/settings.json` and delete `~/.claude/skills/asof/`.

## How it works

The hook does the temporal computation in Python. The SKILL.md teaches Claude how to interpret the verdicts. Claude never does date arithmetic in chat — every gap, age, duration is pre-computed.

See [docs/design.md](../../docs/design.md) for the full design.

## Verify

```bash
asof check
```

Checks that:
- `asof_core` is importable
- Hook scripts are present
- `settings.json` has the entries
- Hook fires produce expected output

## Troubleshooting

- **No AsOf block appears in context.** Expected for casual turns — adaptive rendering. If you've Read a file and externally edited it, the next turn should produce a STALE alert. If not: run `asof check` and verify the PostToolUse hook is wired.
- **`INCOMPATIBLE` notice in output.** Hook and SKILL.md are on incompatible schema versions. Run `pip install --upgrade asof && asof install` to align.
- **Hook timing out.** The hook should run in ~100-300ms. If slower, check `~/.asof/tool_log/<session_id>.jsonl` for size — very long sessions can accumulate large logs. Rotation is V2.

## Schema version

This adapter requires `asof_core` >= 0.1.0.
