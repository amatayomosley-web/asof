# AsOf — Generic adapter

For any harness that isn't Claude Code or Antigravity. Reference implementations for common frameworks live in [examples/](examples/). Roll your own by following the pattern.

## The integration shape

AsOf provides three substrate-agnostic entry points in `asof_core.hooks`:

```python
from asof_core.hooks import session_init, post_tool, watch

# At session start:
banner = session_init(model_id="...", session_id="...")
# Inject `banner` into your model's context.

# After every tool call your harness makes:
post_tool(session_id="...", tool_name="...", tool_input={...})

# Before every user prompt is sent to the model:
verdict_block = watch(session_id="...", prompt_text="...")
# Inject `verdict_block` into the model's context if non-empty.
```

Plus the pull-based oracle:

```python
from asof_core.query import query
result = query("Q3 2025")  # or a file path, URL, model ID, any text
# result["verdict"]: "fresh" | "stale" | "unverifiable" | "unknown"
# result["detail"]: kind-specific (gap, mtime, ETag, etc.)
```

## Reference implementations

- **`examples/langgraph_node.py`** — LangGraph node that wraps the three hook calls into a graph step
- **`examples/crewai_step.py`** — CrewAI step that fires `watch` before each agent message
- **`examples/anthropic_sdk_wrapper.py`** — Direct Anthropic SDK usage with `session_init` + `watch` injected as system content

These are starting points. Copy, adapt to your harness's lifecycle conventions, validate the verdict block actually changes your model's behavior.

## What you provide

Your harness owns:
- The session ID (must be stable per-session)
- The model ID (passed to `session_init` for cutoff lookup)
- The tool-call lifecycle (call `post_tool` after each tool invocation)
- Context injection mechanism (how the `session_init` and `watch` outputs reach your model)
- Tool registration for `asof_query` (if you want the model to be able to query the oracle)

## What AsOf provides

- The conditional-staleness model (mtime + writer-set + invalidation evidence)
- Timestamp parsing (dates, quarters, fiscal references, relative phrases)
- Pattern detection for time-sensitive prompts
- Pre-computed gap arithmetic (so your model never does date math in chat)
- Schema-versioned output that fails loud on prose/hook mismatch
- The session-scoped tool log (`~/.asof/tool_log/<session_id>.jsonl`)
- The watchlist mechanism
- The provenance database (cross-session URL→file tracking, opt-in)
- The CLI (`asof check`, `asof query <target>`)

## Choosing the right wiring

| Your harness | Recommended pattern |
|---|---|
| Single hook event (like Antigravity) | One orchestrator script dispatching by state — see `adapters/antigravity/` |
| Three lifecycle events (like Claude Code) | Three separate scripts mapped 1:1 — see `adapters/claude_code/` |
| In-process Python (LangGraph, CrewAI) | Direct function call from a graph node or step — see examples |
| Direct API usage | Wrap your `messages.create` call with session_init + watch injection — see `anthropic_sdk_wrapper.py` |
| Other LLM SDK | Same shape as Anthropic SDK wrapper, swap the client |

## Schema version

The generic adapter expects `asof_core` schema 0.1.0 or compatible. If you fork or vendor `asof_core`, declare your minimum version per [version.py](../../asof_core/version.py).
