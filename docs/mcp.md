# MCP (Model Context Protocol)

## Use external MCP servers

Their tools appear to the agent as `mcp__<server>__<tool>` and flow through the same
permission cascade.

```bash
aegis mcp add filesystem "npx -y @modelcontextprotocol/server-filesystem /tmp"
aegis mcp test
aegis mcp list
```

Also reads a Claude-Desktop-format `~/.aegis/mcp.json` (`{"mcpServers": {...}}`).

## Be an MCP server

Expose AEGIS's own tools to any MCP client (editors, other agents):

```bash
aegis mcp serve     # JSON-RPC 2.0 over stdio: initialize / tools/list / tools/call
```

Point an MCP client at `aegis mcp serve` and it can use AEGIS's filesystem, shell, web,
memory, and skill tools — gated by your permission policy.
