<p align="center"><img src="assets/banner.svg" alt="AEGIS" width="900"></p>

<p align="center"><b>The local-first agent workbench you actually own.</b><br>
One auditable Python runtime for the terminal, browser dashboard, desktop app, API clients, automation, and MCP.</p>

<p align="center">
  <a href="https://github.com/Alien0013/aegis/actions"><img src="https://github.com/Alien0013/aegis/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
  <img src="https://img.shields.io/badge/runtime-local--first-6fb7d8" alt="local-first">
  <a href="docs/index.md"><img src="https://img.shields.io/badge/docs-current-d8913f" alt="docs"></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#what-aegis-does">What it does</a> ·
  <a href="#the-terminal">Terminal</a> ·
  <a href="#run-and-test">Run &amp; test</a> ·
  <a href="docs/index.md">Docs</a>
</p>

---

AEGIS is a self-hostable agent harness for people who want a capable coding and
operations assistant **on their own machine** — not behind someone else's API. The
same agent loop powers every surface: a full-screen terminal, a local React
dashboard, an Electron desktop app, an OpenAI-compatible API, JSON-RPC, a Python
SDK, ACP, MCP, cron, webhooks, and messaging gateways.

State is local by default. Config, secrets, sessions, memory, traces, evals,
checkpoints, logs, and tool output live under `~/.aegis` or `$AEGIS_HOME`.

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
aegis            # full-screen terminal agent
aegis ui         # local browser dashboard
```

AEGIS starts with a lean, cost-safe tool surface (≈22 live tool schemas; the rest
are loaded on demand). Opt into extra toolsets only when you need them:

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- \
  --toolsets core,browser --skills web-research,summarize
```

<p align="center"><img src="assets/terminal.png" alt="AEGIS terminal" width="860"></p>

<p align="center"><img src="assets/dashboard.png" alt="AEGIS dashboard" width="860"><br>
<sub>The local dashboard opens session-first and includes Chat, Terminal, Live Agents, Overview, Command Center, Models, Tools, Skills, Memory, Persona, Schedules, Kanban, MCP, Channels, Webhooks, Pairing, Accounts, Env, Plugins, Analytics, Files, Logs, Profiles, Docs, System, and Config pages.</sub></p>

## Quickstart

Install, configure a provider, and start chatting:

```bash
aegis setup
aegis secret set ANTHROPIC_API_KEY
aegis model set anthropic claude-sonnet-4-6

aegis
aegis chat -q "summarize this repo"
aegis chat --continue
```

Open the other local surfaces:

```bash
aegis ui --no-open --port 9119       # browser dashboard backend + built UI
aegis desktop                        # Electron shell around the dashboard
aegis serve --port 8790              # OpenAI-compatible API
aegis status                         # terminal status snapshot
```

Use `aegis status`, `aegis tools list`, and `aegis skills list` for live counts
from your current checkout and environment. Optional tools report missing
dependencies instead of failing import. Run `aegis cost status` to inspect live
token overhead, and `aegis models refresh` to pull the latest model/pricing
catalog so `aegis cost` is accurate for any model.

## The Terminal

Run `aegis` on an interactive TTY and it opens a **full-screen Node/Ink terminal**:

- alternate-screen layout with a **fixed header** and a bottom-anchored scroll
  region (`PgUp`/`PgDn`/`Esc`, optional mouse wheel via `AEGIS_TUI_MOUSE=1`)
- structured **tool cards** (status pill, duration, `+adds/-dels` diff stats),
  thinking, message bubbles, and notices — streamed from the agent as events
- a live **status bar**: model, context meter, tokens, cost, reasoning, perms
- a persistent **composer** with slash-command completion (`Tab`), history
  (`↑`/`↓`), and multi-line input (`\` + Enter); `^C` interrupts a turn
- Unicode glyphs with an automatic ASCII fallback

It's driven by an in-process Python WebSocket gateway (`aegis.tui_gateway`). The
Ink bundle ships prebuilt, so only `node` on PATH is needed at runtime — and if
Node is absent (or `AEGIS_CLASSIC_TUI=1`), AEGIS falls back to a pure-Python
full-screen surface, then to a classic line REPL. Every surface shares the same
streaming, slash commands, queued/interruptible turns, `@file`/`@diff`/`@url`
context references, sessions, branching, checkpoints, diff, rollback, goals,
traces, and usage.

## What AEGIS Does

| Area | Current behavior |
| --- | --- |
| Shared runtime | CLI, dashboard, desktop, API, SDK, ACP, MCP, gateway, cron, webhooks, and background work all enter through the same `SurfaceRunner` and `Agent.run` path. Live activity snapshots track phase, active model/tool/subagent state, child subagent cards, and run breadcrumbs across surfaces. |
| Terminal agent | Full-screen Node/Ink TUI (see [The Terminal](#the-terminal)) with a pure-Python and classic-REPL fallback chain. |
| Dashboard | Token-gated FastAPI + React/Vite control panel that opens on sessions, with `/dashboard` as a calmer overview and `/command-center` as a compact sessions/system/usage overlay. Includes chat, terminal, models, tools, skills, memory, schedules, kanban, MCP, channels, webhooks, pairing, provider accounts, env/secrets, plugins, analytics, files, logs, profiles, docs, system, and config. |
| Desktop | Electron app that launches/probes a local dashboard backend on a random port with a random token, shows boot/retry/log states, remembers window settings, and runs from source or packaged installers. |
| Providers | Built-in registry for Anthropic, OpenAI, Codex-compatible paths, Google, OpenRouter, Groq, DeepSeek, Qwen/DashScope, xAI, Mistral, Together, Hugging Face, local OpenAI-compatible endpoints, Ollama, LM Studio, vLLM, and more. API-key auth is the default; OAuth where implemented. The dashboard provider matrix shows auth readiness, redacted live-probe status, fallback chains, context/output limits, pricing, and model capabilities. |
| Tools &amp; permissions | A lean primitive tool surface — file, patch, shell, process, web, browser, LSP, code execution, image generation, vision, subagents, model mixtures, cron/kanban, memory, skills — plus deferred integrations and MCP/plugin tools, all through one registry and permission engine. Cost-safe by default (~22 live schemas; the rest load on demand via `tool_search`). |
| Memory | File-backed `MEMORY.md`/`USER.md` plus pluggable backends (JSONL, HTTP, Honcho, Mem0), FTS5 session recall, redaction/sanitization, session review, and approval-based promotion. |
| Skills | 70+ bundled `SKILL.md` playbooks (coding, devops, automation, data, creative, productivity), skill creation/improvement, and a built-in **automation/watchers** skill (poll RSS/JSON/GitHub on a schedule, react only to what's new). |
| Automation &amp; evals | `aegis cron`, `aegis kanban`, `aegis spec`, `aegis watch`, `aegis bench`, `aegis eval`, `aegis ab`, traces, runs, cost analytics, backup/import/snapshot, and security/debug reports. |
| API &amp; embedding | OpenAI-compatible `/v1/chat/completions` and `/v1/models`, JSON-RPC stdio, Python SDK, ACP stdio server, MCP client, and `aegis mcp serve`. |

## Run And Test

From a clone:

```bash
git clone https://github.com/Alien0013/aegis
cd aegis
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"
```

Terminal and core checks:

```bash
python -m aegis.cli.main --help
python -m aegis.cli.main status
python -m aegis.cli.main tools list
bash scripts/run_tests.sh
bash scripts/verify_all.sh
```

Dashboard:

```bash
aegis ui --no-open --port 9119

# In another terminal for frontend development:
cd web && npm install && npm run dev   # Vite proxies /api to the backend
npm run typecheck && npm run build
scripts/check_web_dist.sh              # verify the committed built bundle
```

Terminal UI (Node/Ink) — rebuild after editing `aegis/tui_ink/src`:

```bash
scripts/build_tui.sh                   # npm install + typecheck + bundle
```

Desktop:

```bash
aegis desktop --doctor
aegis desktop
cd desktop && npm install && npm run test:desktop && npm run pack
```

`npm run dist:linux|win|mac` build installer artifacts when the local platform
and signing inputs allow it.

OpenAI-compatible API:

```bash
aegis serve --port 8790
curl -s http://127.0.0.1:8790/v1/models
curl -s http://127.0.0.1:8790/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"default","messages":[{"role":"user","content":"Say hello from AEGIS"}]}'
```

Gateway and automation:

```bash
aegis gateway --channels telegram,discord
aegis cron list
aegis trace list
aegis security audit
```

Live provider calls and live messaging-platform tests require configured
credentials. Use `aegis doctor --probe` only when you intentionally want network
provider probes.

## Architecture

<p align="center"><img src="assets/system-map.svg" alt="AEGIS architecture map" width="900"></p>

Every surface enters through the same runtime, so behavior stays consistent: a
disabled tool stays disabled everywhere; a session can be resumed from another
surface; memory, permissions, traces, run rows, and usage are shared.

<p align="center"><img src="assets/runtime-loop.svg" alt="AEGIS runtime loop" width="900"></p>

The loop builds context from rules, memory, skills, and references; routes to the
selected provider; runs tool calls through policy; wraps untrusted outputs; emits
events; persists traces and usage; and can review completed work for memory or
skill candidates.

## Repository Layout

```text
aegis/                  Python package
  agent/                loop, context, compaction, governance, events
  providers/            provider registry, transports, auth, fallback
  tools/                registry, permissions, built-ins, browser, LSP, process, kanban
  gateway/              channel adapters, pairing, routing, delivery queue
  mcp/                  client and server support
  lsp/                  persistent language-server service
  cli/                  parser, REPL, menus, full-screen TUI launcher (tui_ink)
  tui_gateway.py        local WebSocket gateway driving the Node/Ink terminal UI
  tui_ink/              Node/Ink terminal UI (TypeScript → prebuilt dist/entry.js)
  builtin_skills/       bundled SKILL.md packages
  static/web_dist/      built dashboard served by aegis ui
web/                    React + Vite dashboard source
desktop/                Electron shell
docs/                   install, CLI, dashboard, API, SDK, providers, security
assets/                 README images and diagrams
scripts/                test, build, and verification helpers
tests/                  offline regression suite
```

## Roadmap

The local runtime, terminal, dashboard, desktop shell, API, SDK, MCP, gateway,
automation, tracing, eval, memory, and skills surfaces are all in the repo and
covered by `aegis verify` / `bash scripts/verify_all.sh`. What's intentionally
**not** claimed here, because it needs resources outside this repo:

| Area | What's outstanding |
| --- | --- |
| Desktop release | Real signed Windows / notarized macOS artifacts need release credentials + CI. |
| Live platforms | Telegram, Discord, Slack, and webhook smoke needs real platform test accounts. |
| Live providers | Provider probes need intentionally configured credentials and network access. |
| Enterprise auth | Native AWS Bedrock / Azure AD identity providers (API-key paths already work). |

## Good To Know

- Optional features need extra dependencies or credentials: browser/computer,
  LSP, voice, vision, provider probes, some gateway channels, and some skills.
- The dashboard binds to `127.0.0.1` by default. Keep the token private if you
  bind it elsewhere or place it behind your own auth.
- Back up local state with `aegis backup`; inspect paths with `aegis status`.
- Update with `aegis update`. Remove with `./uninstall.sh`; add `--purge` only if
  you also want to delete `~/.aegis`.

## License

MIT. Your keys, your data, your machine.
</content>
