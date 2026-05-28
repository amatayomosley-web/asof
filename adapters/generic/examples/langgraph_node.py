"""LangGraph reference integration for AsOf.

Drop this node into your graph immediately before the model-call node.
It produces the time-aware system content that should be prepended to
the model's message list.

Example graph:
    graph = StateGraph(AgentState)
    graph.add_node("asof", asof_node)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_edge("asof", "plan")
    graph.add_edge("plan", "act")
    graph.set_entry_point("asof")

The AsOf node enriches the state with a `temporal_context` field. The
downstream plan/act nodes prepend it to the model's system message.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from asof_core.hooks import session_init, watch


def asof_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph node that produces temporal context.

    Expected state fields:
        session_id: str — graph session identifier
        model_id: str — the model being used
        user_prompt: str — the current user message
        is_first_turn: bool — True only on the first invocation per session

    Returns updated state with `temporal_context` field populated.
    """
    session_id = state.get("session_id", "langgraph-default")
    model_id = state.get("model_id")
    user_prompt = state.get("user_prompt", "")
    is_first_turn = state.get("is_first_turn", False)
    now = datetime.now(timezone.utc)

    pieces: list[str] = []

    if is_first_turn and model_id:
        pieces.append(session_init(model_id=model_id, session_id=session_id, now=now))

    block = watch(session_id=session_id, prompt_text=user_prompt, now=now)
    if block:
        pieces.append(block)

    state["temporal_context"] = "\n".join(pieces)
    return state


def post_tool_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph node to run after tool calls. Logs the tool use into
    AsOf's session-scoped log so the next `asof_node` invocation can
    compute freshness verdicts.

    Expected state fields (in addition to those used by asof_node):
        last_tool_name: str
        last_tool_input: dict
    """
    from asof_core.hooks import post_tool

    session_id = state.get("session_id", "langgraph-default")
    tool_name = state.get("last_tool_name")
    tool_input = state.get("last_tool_input", {})

    if tool_name:
        post_tool(
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            now=datetime.now(timezone.utc),
        )
    return state
