# Tools & Permissions

30 built-ins: `read_file`, `write_file`, `edit_file`, `apply_patch`, `list_dir`, `glob`,
`search`, `bash`, `process`, `download`, `http_request`, `web_fetch`, `web_search`,
`browser`, `cloud_browser`, `computer`, `generate_image`, `cloud_image`, `transcribe`,
`speak`, `execute_code`, `spawn_subagent`, `lsp`, `github`, `tool_search`, `memory`,
`skill`, `session_search`, `todo_write`, `schedule_task` — plus every connected MCP and
plugin tool. Group them with `tools.toolsets` (add `browser`, `computer`, `voice`, `lsp`).

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
