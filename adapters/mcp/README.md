# AsOf — MCP server adapter

Exposes AsOf's temporal-awareness **oracle** to any [Model Context
Protocol](https://modelcontextprotocol.io) client (Claude Desktop, Cursor, and
other MCP-compatible agents).

AsOf's host adapters (Claude Code, Antigravity) *push* freshness verdicts via
lifecycle hooks. MCP is request/response, so this adapter is **pull-only**: the
agent calls these tools on demand. It gives you the on-demand oracle, not the
proactive per-turn surfacing — for that, use the hook adapters.

## Install

```bash
pip install "asoftime[mcp]"
```

## Register with a client

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "asof": { "command": "asof-mcp" }
  }
}
```

Or run it directly over stdio: `asof-mcp` (equivalently `python -m adapters.mcp.server`).

## Tools

| Tool | Purpose |
|------|---------|
| `asof_now()` | Current real-world date/time (UTC) — anchor reasoning in the present, not a training-era "now". |
| `asof_query(target, kind_hint?)` | Freshness verdict for a file path, URL, timestamp/date string, model ID, or free text. Returns `fresh` / `stale` / `unverifiable` / `unknown` with the computed gap. |
| `asof_cutoff(model_id)` | A model's training cutoff and how long ago it was, so the caller knows how stale its parametric knowledge is. |

## Limitations

- **Pull-only.** No proactive staleness alerts — the agent must choose to ask.
- **No session baseline.** With no tool-log, `asof_query` on a *file* reports
  its current mtime rather than a read-relative staleness verdict. The
  read-relative model lives in the push-hook adapters.
- **stdio transport only.**
