"""Direct Anthropic SDK reference integration for AsOf.

Wraps a `messages.create` call to:
1. Inject session_init output as the first system message on session start
2. Inject the watch verdict block before each turn (if non-empty)
3. Log tool calls via post_tool after each tool use

Use:
    from anthropic import Anthropic
    from asof_anthropic_wrapper import AsOfClient

    client = AsOfClient(
        Anthropic(),
        session_id="my-session",
        model="claude-opus-4-7",
    )

    response = client.create_message(
        prompt="What's the current price of AAPL?",
    )

The wrapper handles the AsOf bookkeeping behind the scenes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from asof_core.hooks import session_init, post_tool, watch


class AsOfClient:
    """Thin wrapper over an Anthropic client with AsOf integration.

    Stateful: tracks whether session_init has been called for the
    current session_id, and injects the directive only once.
    """

    def __init__(self, anthropic_client: Any, *, session_id: str = None, model: str = None):
        self.client = anthropic_client
        self.session_id = session_id or str(uuid.uuid4())
        self.model = model or "claude-opus-4-7"
        self._init_fired = False
        self._conversation_messages: list[dict] = []

    def create_message(self, prompt: str, **kwargs) -> Any:
        """Send a message with AsOf temporal context injected.

        `kwargs` pass through to the underlying client.messages.create.
        """
        now = datetime.now(timezone.utc)

        # First call this session — inject session_init as system content
        system_parts: list[str] = []
        if not self._init_fired:
            system_parts.append(session_init(
                model_id=self.model,
                session_id=self.session_id,
                now=now,
            ))
            self._init_fired = True

        # Per-turn watch
        watch_block = watch(
            session_id=self.session_id,
            prompt_text=prompt,
            now=now,
        )
        if watch_block:
            system_parts.append(watch_block)

        # Merge with any user-provided system content
        existing_system = kwargs.pop("system", "")
        if existing_system and isinstance(existing_system, str):
            full_system = "\n\n".join([*system_parts, existing_system])
        else:
            full_system = "\n\n".join(system_parts)

        self._conversation_messages.append({"role": "user", "content": prompt})

        response = self.client.messages.create(
            model=self.model,
            system=full_system if full_system else None,
            messages=self._conversation_messages,
            **kwargs,
        )

        # Extract assistant response and log any tool uses
        assistant_content = []
        for block in response.content:
            if hasattr(block, "type"):
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    post_tool(
                        session_id=self.session_id,
                        tool_name=block.name,
                        tool_input=block.input,
                        now=datetime.now(timezone.utc),
                    )

        if assistant_content:
            self._conversation_messages.append({
                "role": "assistant",
                "content": assistant_content,
            })

        return response
