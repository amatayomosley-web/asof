"""Hook entry points for AsOf.

Three substrate-agnostic functions that adapters wire into substrate-
specific hook events:

- session_init: called once at session start. Emits the directive +
  training cutoff. Initializes the session-scoped tool log.

- post_tool: called after every tool call. Captures the tool's target
  and metadata (including file mtime at read time) into the tool log.

- watch: called per user-prompt-submit. Reads the tool log + current
  filesystem state + parses the prompt. Produces the adaptive verdict
  block.

Each adapter maps its substrate's hook events to these functions:
- Claude Code: SessionStart → session_init, PostToolUse → post_tool,
  UserPromptSubmit → watch
- Antigravity: single PreInvocation orchestrator dispatches based on
  invocationNum + conversationId (per Current's Turn 134 design)
- Generic: caller invokes the functions directly from their harness
"""
from __future__ import annotations

from asof_core.hooks.session_init import session_init
from asof_core.hooks.post_tool import post_tool
from asof_core.hooks.watch import watch

__all__ = ["session_init", "post_tool", "watch"]
