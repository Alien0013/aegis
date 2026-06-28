# Tools & Permissions

126 registered tools cover files, search, shell, processes, web, HTTP, downloads,
browser automation, UI verification, computer control, image generation, speech,
code execution, subagents, mixture-of-agents, LSP, GitHub, memory, skills, session
recall, repo maps, semantic code search, cron jobs, dependency audit, agent state,
cloud backends, and every connected MCP/plugin tool.

On a bare install, AEGIS enables the `core` toolset but defers uncommon schemas,
so a normal CLI turn ships about 22 live tool schemas instead of the whole
registry. Enable optional toolsets with `tools.toolsets` (`browser`, `computer`,
`voice`, `vision`, `web`, `lsp`, `mcp`) when their dependencies and credentials
are configured.

```bash
aegis cost status
aegis cost optimize   # repair older broad installs back to the lean CLI profile
```

`agent_state` exposes shared runtime state to every surface: current session,
recent sessions, session branching, linked run/trace breadcrumbs, eval runs, and
background task status.

## Deferred schemas (context economy)

Rarely-used tools ship **name-only**: a stable index in the system prompt lists them,
and their full parameter schemas stay off the wire until the model activates one with
`tool_search` (activation is session-sticky). Configure with:

```yaml
tools:
  defer_schemas: true
  deferred: [source:alias, toolset:browser, toolset:mcp, generate_image, github, …]
```

Deferred entries can be exact names or selectors:

- `mcp:*` or `source:mcp` defers connected MCP tools and resource/prompt helpers.
- `plugin:*` or `source:plugin` defers plugin-registered tools.
- `toolset:mcp` defers every tool in a toolset.
- `glob:mcp__filesystem__*` or `mcp__filesystem__*` defers by name pattern.

`tool_search` activates matching deferred tools for the current session and
prints the schema that was loaded.

## Typed subagents

`spawn_subagent` takes `agent_type`:

| type | tools | use for |
|---|---|---|
| `general` (default) | normal child tools | implementation work |
| `explore` | read-only | fan-out search/research |
| `plan` | read-only | step-by-step implementation plans |
| `review` | read-only | code review with file:line findings |

Leaf subagents cannot recurse or touch shared side-effect tools:
`spawn_subagent`, `clarify`, `memory`, `send_message`, and `execute_code` are
withheld. `role: orchestrator` can regain `spawn_subagent` when
`agent.max_spawn_depth` allows it; the other shared side-effect tools remain
blocked. Permission prompts inside child work auto-deny by default, or
auto-approve when `delegation.subagent_auto_approve: true`.

`continue_id` sends a follow-up to a previous subagent with its context intact;
`background: true` runs async and wakes the parent agent when done.
Subagent tasks use the shared context-reference expander, so prompts like
`review @file:src/app.py` attach the same file context as CLI, dashboard, RPC,
gateway, and SDK turns.

## Background completion wakeups

`process start` (and background subagents) notify the agent when they finish — the
next turn begins with a `<background_completions>` block (results treated as
untrusted data), and gateway chats get an announce-back message.

```bash
aegis tools            # list
aegis tools status     # which tool-gateway backends are configured
```

## Permission cascade

Every side-effecting tool flows through:
`hardline blocklist → deny_groups → exec_mode → allowlist → approval`.

```yaml
tools:
  exec_mode: smart          # deny | allowlist | ask | smart | auto | full
  deny_groups: []           # fs, runtime, network, automation
  allowlist: ["git ", "ls"]
```

- **Hardline blocklist** refuses catastrophic commands (`rm -rf /`, fork bombs,
  `curl|bash`) even in `full`/`--yolo`.
- **smart** mode asks an auxiliary model to assess risk.
- Pre-execution **security scan** (`security.scan_enabled`) flags injection/exfiltration.

## Terminal backends (sandboxing)

```yaml
tools:
  terminal_backend: docker   # local | docker | ssh | singularity | modal
  allow_local_fallback: false  # fail closed if the sandbox is down
```

## Web search backends

`web.search_backend: auto | duckduckgo | brave | tavily | serper` (set the matching
`*_API_KEY`).

## Code intelligence (LSP)

Persistent language servers (13 languages bundled — Python, TS/JS, Go, Rust, C/C++,
Bash, YAML, PHP, Lua, Terraform, Docker, Zig, Ruby) stay alive per project root.
Missing servers auto-install into `<home>/lsp` (npm/go/pip).

- **Edit feedback**: after every `write_file`/`edit_file` inside a git project, only
  the diagnostics *introduced by that edit* are appended to the tool result (a
  diff-based line-shift keeps pre-existing ones quiet). Toggle with `lsp.on_edit`.
- The `lsp` tool adds `rename`, `symbols`, `status`, and `restart` on top of
  `diagnostics` / `hover` / `definition` / `references`.

```yaml
lsp:
  on_edit: true          # report new diagnostics after edits
  auto_install: true     # install missing servers into <home>/lsp
  servers: {".py": "pylsp"}   # per-extension command override
```
