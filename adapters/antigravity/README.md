# AsOf — Google Antigravity Adapter

Temporal awareness and file-freshness protection for Google Antigravity developer sessions.

Since Antigravity natively supports a single `PreInvocation` hook stage and processes commands via a multi-step executor, this adapter dynamically synthesizes `SessionStart`, `UserPromptSubmit`, and `PostToolUse` events to prevent token-burn and redundant injections.

## Install

Run the installer script from the root of the adapter directory:

```powershell
python install.py
```

The installer will:
1. Verify the current environment.
2. Create directories at `~/.gemini/config/hooks/asof/` and `~/.gemini/config/plugins/asof/`.
3. Copy `asof_antigravity_orchestrator.py` to the hook folder.
4. Copy `SKILL.md` to the plugins folder.
5. Idempotently update/patch `~/.gemini/config/hooks.json` to register the `PreInvocation` orchestrator hook.

After installation, restart your Antigravity agent terminal session.

## Configuration & Environment

The orchestrator reads active settings and environment variables during invocation:

- `GEMINI_MODEL`: Set this to specify the active Gemini model (e.g., `gemini-3.5-pro`, `gemini-3.5-flash`, `gemini-3.1-pro-preview`) so that the adapter looks up the correct training cutoff date.
- State and logs are maintained inside `~/.asof/state/` and `~/.asof/tool_log/`.

## How It Works

Because Antigravity executes in a multi-step loop, running the hook on every tool step would cause massive token overhead. The orchestrator solves this using **Event Synthesis**:

1. **SessionStart (Wake)**:
   - Tracks the active `conversationId` inside `~/.asof/state/session_state.json`.
   - When a new `conversationId` is encountered, it triggers the session wake sequence, injecting the time-decay grounding directive.

2. **UserPromptSubmit**:
   - Gates checks to `invocationNum == 0` on subsequent turns.
   - Parses the latest user prompt from `transcript.jsonl` for time-sensitive phrasing (Tiers 1 & 2) and file paths.
   - Compares the active workspace file modification times (`mtime`) with when they were read, injecting stale alerts if any files were changed externally.

3. **PostToolUse**:
   - Wakes on `invocationNum > 0` (subsequent executor turns).
   - Reads the active `transcript.jsonl` dynamically to extract completed tool calls and register them in the local tool logs (`~/.asof/tool_log/<conv_id>.jsonl`) for freshness tracking.

## Uninstall

To uninstall the adapter:
1. Open `~/.gemini/config/hooks.json` and remove the `PreInvocation` entry pointing to `asof_antigravity_orchestrator.py`.
2. Delete the folders `~/.gemini/config/hooks/asof/` and `~/.gemini/config/plugins/asof/`.

## Troubleshooting

- **No time alerts injected**:
  - The watch logic uses **Adaptive Rendering**. It is completely silent on turns with no temporal cues and no stale files.
  - If a file is read and edited outside the session, the next user command should prompt a `STALE` alert.
- **Hook failures blocking commands**:
  - The orchestrator script is designed with strict try-catch handlers and exits 0 on all errors. It will never block or interfere with your Antigravity command loop.
