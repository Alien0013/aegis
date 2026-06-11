# MCP (Model Context Protocol)

## Use external MCP servers

Their tools appear to the agent as `mcp__<server>__<tool>` and flow through the same
permission cascade.

```bash
aegis mcp add filesystem "npx -y @modelcontextprotocol/server-filesystem /tmp"
aegis mcp test
aegis mcp list
aegis mcp tools filesystem
```

Also reads a Claude-Desktop-format `~/.aegis/mcp.json` (`{"mcpServers": {...}}`).

## Catalog and filters

AEGIS can install local catalog entries from `mcp.catalog` in `config.yaml`:

```yaml
mcp:
  catalog:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      description: Local filesystem tools
      tool_filter:
        include: ["read_file", "list_directory"]
```

```bash
aegis mcp catalog
aegis mcp install filesystem
aegis mcp tools filesystem
```

Each configured server can include `tool_filter.include` and/or
`tool_filter.exclude` to control which remote tools are surfaced to AEGIS. MCP
clients also expose `list_resources()` and `list_prompts()` for dashboard and
tooling surfaces that want to inspect server resources without invoking tools.

When a server advertises resources or prompts, AEGIS also registers utility tools:

- `mcp__<server>__read_resource` calls MCP `resources/read` for a URI.
- `mcp__<server>__get_prompt` calls MCP `prompts/get` with optional arguments.

`aegis mcp tools <server>` prints remote tools plus advertised resources and
prompts so you can hand the right URI or prompt name to the agent.

Any AEGIS entry surface that supports prompt references can also attach an MCP
resource directly:

```text
Summarize @mcp:filesystem:file:///workspace/README.md
```

The format is `@mcp:<server>:<resource-uri>`.

## Be an MCP server

Expose AEGIS's own tools to any MCP client (editors, other agents):

```bash
aegis mcp serve     # JSON-RPC 2.0 over stdio: initialize / tools/list / tools/call
```

Point an MCP client at `aegis mcp serve` and it can use AEGIS's filesystem, shell, web,
memory, and skill tools — gated by your permission policy.

Server mode lists only tools that are currently visible through the active tool
registry and toolset filters. Tool calls receive a full local `ToolContext`
including cwd, config, session, memory, skills, and a lightweight agent handle,
so memory and skill tools behave like they do from the normal agent loop.
