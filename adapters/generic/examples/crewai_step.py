"""CrewAI reference integration for AsOf.

Wraps an agent's `execute` method to inject temporal context before
each call. The agent sees the AsOf block as part of its task description.

Use:
    from crewai import Agent, Task
    from asof_core_examples.crewai_step import wrap_agent_with_asof

    analyst = Agent(role="Financial Analyst", ...)
    analyst = wrap_agent_with_asof(analyst, session_id="my-session")

    task = Task(description="Analyze the Q3 2025 earnings", agent=analyst)
    crew = Crew(agents=[analyst], tasks=[task])
    crew.kickoff()

The wrapper injects AsOf's directive + per-turn watch into the task
description automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from asof_core.hooks import session_init, post_tool, watch


_SESSION_INIT_FIRED: set[str] = set()


def wrap_agent_with_asof(agent: Any, *, session_id: str, model_id: str = None) -> Any:
    """Monkey-patch an agent's `execute_task` (CrewAI 0.x+) to inject
    AsOf temporal context into each task description.

    Best-effort wrapping. If the underlying CrewAI API changes, adjust
    the wrapped method name.
    """
    if not hasattr(agent, "execute_task"):
        raise AttributeError(
            "Agent does not have execute_task method. "
            "Check CrewAI version compatibility."
        )

    original = agent.execute_task

    def wrapped(task, *args, **kwargs):
        now = datetime.now(timezone.utc)

        # Session-init fires once per session_id
        if session_id not in _SESSION_INIT_FIRED and model_id:
            init_block = session_init(model_id=model_id, session_id=session_id, now=now)
            _SESSION_INIT_FIRED.add(session_id)
            task.description = init_block + "\n\n" + (task.description or "")

        # Per-task watch (acts like UserPromptSubmit)
        watch_block = watch(
            session_id=session_id,
            prompt_text=task.description or "",
            now=now,
        )
        if watch_block:
            task.description = watch_block + "\n\n" + (task.description or "")

        return original(task, *args, **kwargs)

    agent.execute_task = wrapped
    return agent


def log_tool_call(*, session_id: str, tool_name: str, tool_input: dict) -> None:
    """Helper for CrewAI tools — call this from your tool's run method
    so AsOf can track the call for freshness verdicts."""
    post_tool(
        session_id=session_id,
        tool_name=tool_name,
        tool_input=tool_input,
        now=datetime.now(timezone.utc),
    )
