<p align="center"><img src="assets/banner.svg" alt="AEGIS" width="900"></p>

<p align="center"><b>The terminal AI agent you actually own.</b><br>
Any model, many surfaces, local-first state, one auditable Python core.</p>

<p align="center">
  <a href="https://github.com/Alien0013/aegis/actions"><img src="https://github.com/Alien0013/aegis/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
  <img src="https://img.shields.io/badge/tests-973%20passing-brightgreen" alt="tests">
  <img src="https://img.shields.io/badge/providers-29-d8913f" alt="providers">
  <img src="https://img.shields.io/badge/tools-45-6fb7d8" alt="tools">
  <img src="https://img.shields.io/badge/skills-41-7ecf8f" alt="skills">
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#features">Features</a> ·
  <a href="docs/index.md">Docs</a>
</p>

---

AEGIS is a self-hostable agent harness for people who want the power of a coding
agent without handing the whole workflow to a remote black box. It runs from your
terminal, browser, desktop app, messaging channels, API clients, and MCP tools,
while sharing the same provider routing, permissions, memory, sessions, traces,
and local state.

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
aegis            # terminal agent
aegis ui         # local browser dashboard
```

<p align="center"><img src="assets/terminal.png" alt="AEGIS terminal" width="860"></p>

<p align="center"><img src="assets/dashboard.png" alt="AEGIS dashboard" width="860"><br>
<sub>The real local dashboard: Overview, Chat, Sessions, Models, Tools, Skills, Memory, Schedules, MCP, Channels, Webhooks, Plugins, Keys, Files, Logs, Profiles, System, Config, and Analytics.</sub></p>

## Why AEGIS

| Capability | What it means |
|---|---|
| **Local-first control** | Sessions, memory, config, auth, traces, evals, and tool outputs live under `~/.aegis` or `$AEGIS_HOME`. |
| **Model choice** | 29 provider presets: Anthropic, OpenAI, Codex, Google, OpenRouter, Groq, DeepSeek, Qwen/DashScope, xAI, MiniMax, Mistral, Together, Ollama, LM Studio, vLLM, and more. |
| **One runtime** | CLI, dashboard, desktop, gateway, OpenAI-compatible API, JSON-RPC, Python SDK, ACP, and MCP all use the same agent loop. |
| **Practical toolset** | 45 registered tools, with 37 visible by default on a bare install and optional browser/computer/LSP/voice/vision tools enabled by toolset. |
| **Memory and learning** | Durable `MEMORY.md`/`USER.md`, FTS5 session recall, external memory providers, session review, and skill promotion. |
| **Safety rails** | Hardline command blocklist, pre-exec security scanning, permission modes, sensitive file guards, sandbox backends, checkpoints, diff, rollback, and redaction. |
| **Public-ready operations** | Doctor, backup/import/snapshot, update/uninstall, security audit, cost analytics, traces, evals, bench tasks, AB replay, ambient test watch, and budget governance. |

## Architecture

<p align="center"><img src="assets/system-map.svg" alt="AEGIS architecture map" width="900"></p>

Every surface enters through the same `SurfaceRunner` and `Agent.run` loop. That
keeps behavior consistent: a tool disabled in the dashboard is disabled for the
CLI; a session started in the terminal can be searched later; a gateway reply,
cron job, API call, and desktop chat all see the same memory and permissions.

<p align="center"><img src="assets/runtime-loop.svg" alt="AEGIS runtime loop" width="900"></p>

The loop builds context from rules, memory, skills, and references; routes to the
selected model; runs tool calls through policy; wraps untrusted results; emits
events; persists traces and usage; and can review completed work for memory or
skill candidates.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
```

The installer finds Python 3.10+, creates an isolated venv at `~/.aegis/venv`,
installs the package, creates a global `aegis` launcher, and starts guided setup.

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --core
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --advanced
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --verify
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --skip-browser
```

From a clone:

```bash
git clone https://github.com/Alien0013/aegis
cd aegis
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[all]"
bash scripts/run_tests.sh
```

Update with `aegis update`. Remove with `./uninstall.sh`; add `--purge` only if
you also want to delete `~/.aegis`.

## Quickstart

```bash
aegis setup
aegis secret set ANTHROPIC_API_KEY
aegis model set anthropic claude-sonnet-4-6

aegis
aegis chat -q "summarize this repo"
aegis chat --continue
aegis ui
aegis desktop
```

OpenAI-compatible local API:

```bash
aegis serve --port 8790
```

Python SDK:

```python
from aegis import AegisClient

client = AegisClient()
result = client.run("Summarize this repository", title="repo summary")
print(result.text)
```

## Features

### Surfaces

- `aegis` terminal REPL with streaming, slash commands, tool trail, checkpoints, diff, rollback, sessions, branch/resume, learning, traces, and usage.
- `aegis ui` local React/Vite dashboard with chat, sessions, models, tools, skills, memory, schedules, MCP, channels, webhooks, plugins, keys/env, files, logs, profiles, system facts, config, analytics, traces, runs, and agents.
- `aegis desktop` Electron shell around the local dashboard.
- `aegis gateway` for Telegram, Discord, Slack, Signal, Matrix, Email, webhooks, and ntfy.
- `aegis serve` OpenAI-compatible `/v1/chat/completions` and `/v1/models`.
- `aegis rpc`, Python SDK, ACP stdio server, MCP client, and `aegis mcp serve`.

### Providers and auth

29 provider presets are available through one config surface. API-key auth works
for all compatible providers; OAuth/PKCE is supported where implemented. The
runtime also supports custom OpenAI-compatible `base_url`, fallback chains, model
metadata, provider probes, and credential pool configuration.

### Tools

AEGIS registers 45 tools. The default bare install exposes 37 model-visible tools:
file read/write/edit, apply patch, directory and glob search, ripgrep search,
shell, background process management, system status, secrets, web fetch/search,
HTTP request, download, todos, memory, skills, skill management, subagents,
mixture of agents, image generation, execute-code, cron jobs, dependency audit,
session search, repo map, semantic code search, agent state, GitHub, tool search,
cloud image, cloud browser, and outgoing messages.

Optional toolsets add browser automation, UI verification, computer control, LSP
code intelligence, vision analysis, web extraction, speech-to-text, and TTS.
Connected MCP and plugin tools join the same registry.

### Safety and permissions

Dangerous tool calls pass through:

```text
hardline blocklist -> deny groups -> exec mode -> allowlist -> approval
```

The agent refuses catastrophic commands even in permissive modes, scans commands
and memory entries for injection/exfiltration patterns, wraps tool results as
untrusted data, redacts secrets in learning flows, guards sensitive paths, and
supports local, Docker, SSH, Singularity, and Modal terminal backends.

### Memory, skills, and learning

- Built-in file memory: `MEMORY.md`, `USER.md`, and `history.jsonl`.
- SQLite sessions with FTS5 search, browse/read/scroll recall, lineage, branching, and archive support.
- 41 bundled `SKILL.md` packages; 38 are available on a bare environment, while document/Kubernetes skills are gated by their runtime requirements.
- Session review can extract memory candidates and propose skill updates with redaction and approval controls.
- External memory provider hooks support JSONL, HTTP-style adapters, Honcho, and Mem0 where configured.

### Automation and evaluation

- `aegis cron` schedules recurring or one-shot agent jobs.
- `aegis kanban` manages a SQLite task board with dependencies, runs, comments, workers, lanes, and retry state.
- `aegis spec` tracks persistent requirements, design, and task state.
- `aegis bench` runs end-to-end task benchmarks.
- `aegis eval` replays offline eval suites.
- `aegis ab` replays a session on a different model and diffs the result.
- `aegis watch` runs project tests on file changes.
- `aegis budget` reports spend/latency governance and downshift state.

### Operations

```bash
aegis doctor --probe
aegis status
aegis security
aegis backup
aegis snapshot
aegis trace list
aegis cost --days 30
aegis insights
```

## Repository layout

```text
aegis/                  Python package
  agent/                loop, context, compaction, governance, events
  providers/            provider registry, transports, auth, fallback
  tools/                registry, permissions, built-ins, browser, LSP, process, kanban
  gateway/              channel adapters, pairing, routing, durable delivery queue
  mcp/                  client and server support
  lsp/                  persistent language-server service
  cli/                  parser, REPL, menus
  builtin_skills/       bundled SKILL.md packages
  static/web_dist/      built dashboard served by aegis ui
web/                    React + Vite dashboard source
desktop/                Electron shell
docs/                   install, providers, tools, gateway, MCP, SDK, security, tracing/evals
assets/                 README images and diagrams
scripts/                test, build, and verification helpers
tests/                  offline regression suite
```

## Public Release Notes

I would treat these as the main flags before a wider launch:

- Keep README/docs counts tied to CI or a script so they do not drift again.
- Use real screenshots, not idealized mockups, whenever the README shows product UI.
- Make optional features obvious: browser, computer, LSP, voice, vision, some skills, and some providers need extra deps or credentials.
- Keep dashboard auth guidance prominent if users bind outside `127.0.0.1`.
- Continue running `bash scripts/run_tests.sh` before release; current local result is `973 passed`.

## Develop

```bash
pip install -e ".[dev]"
bash scripts/run_tests.sh
```

The test runner strips real credentials, pins UTC, uses a throwaway `AEGIS_HOME`,
and runs the same lint gate used in CI.

## License

MIT. Your keys, your data, your machine.
