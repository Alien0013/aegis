<p align="center"><img src="assets/banner.svg" alt="AEGIS" width="760"></p>

<p align="center">
  <a href="https://github.com/Alien0013/aegis/actions"><img src="https://github.com/Alien0013/aegis/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
  <img src="https://img.shields.io/badge/tests-97%20passing-brightgreen" alt="tests">
  <img src="https://img.shields.io/badge/providers-26-blueviolet" alt="providers">
  <img src="https://img.shields.io/badge/tools-30-blueviolet" alt="tools">
</p>

# AEGIS — the terminal agent you actually own

**One command installs a complete AI agent that lives in your terminal, talks to any
model, runs on your machine, and learns as it goes.** AEGIS is an open, self-hostable
alternative to Hermes Agent and OpenClaw — with their capabilities in **~11k auditable
lines** instead of hundreds of thousands.

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
aegis
```

<p align="center"><img src="assets/screenshot.svg" alt="AEGIS session" width="700"></p>

## Why AEGIS is different

|  | What it means |
|---|---|
| 🪶 **Tiny, auditable core** | ~11k lines you can actually read and trust. OpenClaw is ~434k; Hermes is huge. Same capability, none of the sprawl. |
| 🔌 **Truly model-agnostic** | 26 providers (Claude, GPT, Gemini, Llama, DeepSeek, Qwen, local Ollama…) behind one interface, with **API-key *and* OAuth** login, fallback chains, credential pools, and per-prompt routing. |
| 🧠 **It actually learns** | A real closed loop — reviews finished sessions, extracts memory + skills (redacted), and promotes them on your approval. Plus FTS5 cross-session recall. |
| 🛡️ **Safe by default** | A permission cascade with a hardline blocklist (refuses `rm -rf /` even in yolo), pre-exec scanning, **fail-closed** docker/ssh/singularity/modal sandboxes, and untrusted-tool-result wrapping against prompt injection. |
| 📡 **Everywhere you are** | One agent serving CLI, Telegram, Discord, Slack, Signal, Matrix, Email, and webhooks — with voice-memo transcription and a durable, retrying delivery queue. |
| 🧰 **Batteries included** | 30 tools, 24 skills + hub import, MCP (client **and** server), an OpenAI-compatible API, a web dashboard, cron, and a one-line installer. |
| 🔓 **Yours** | MIT, self-hosted, no subscription, no lock-in. Your keys, your data, your machine. |

> Built in the spirit of **NanoClaw**: do what Hermes and OpenClaw do, but keep the
> whole thing small enough to understand in an afternoon.

## Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
```

The installer (like Hermes) finds Python 3.10+, builds an isolated venv at
`~/.aegis/venv`, installs AEGIS, drops a global `aegis` command on your PATH, and
grabs ripgrep if missing. When a terminal is attached it immediately launches the
guided onboarding flow (provider, API key or OAuth, web tools, optional
channels, dashboard, and workspace files) using `/dev/tty`, so `curl | bash`
prompts work correctly. In a real terminal it uses arrow-key menus and
Space-toggle checkboxes; in scripts it falls back to simple text prompts.
Skip onboarding with `--skip-onboard` or `AEGIS_ONBOARD=0`; automation can use
`--no-prompt`, `--dry-run`, and `--verify`.
Windows: `irm …/install.ps1 | iex`.
Everything in one go:

```bash
AEGIS_EXTRAS=all curl -fsSL …/install.sh | bash   # + browser, computer, Discord, Slack
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --advanced
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --no-prompt --verify
```

From a clone, or for development:

```bash
git clone <repo> aegis && cd aegis
./install.sh                       # one-line, isolated, global command
# — or the manual/editable route —
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[all]"            # core + every extra
playwright install chromium        # if you took the browser extra
aegis doctor
```

Keep it current with `aegis update`. Remove with `./uninstall.sh` (`--purge` to also
delete `~/.aegis`). Optional extras: `.[browser]`, `.[computer]`, `.[discord]`,
`.[slack]` — everything else (providers, OAuth, MCP, marketplace, gateway, serve,
voice) is in the core install.

## Quick start

```bash
# 1. run or re-run guided onboarding
aegis setup

# or point it at a provider manually
aegis config set ANTHROPIC_API_KEY  sk-ant-...        # Claude
aegis config set OPENAI_API_KEY     sk-...            # OpenAI
aegis auth login openai                               # …or OAuth instead of a key
aegis model set ollama llama3.1                       # …or fully local, no key

# 2. talk to it
aegis                       # interactive REPL (streaming, slash commands)
aegis chat -q "summarize the files in this folder"
aegis chat --continue      # resume your last session

# 3. run it as a service on chat platforms
export TELEGRAM_BOT_TOKEN=...
aegis gateway --channels telegram,cli
```

## Providers (all support API key; Anthropic/OpenAI/Google also OAuth)

`anthropic`, `openai`, `google` (Gemini), `openrouter`, `groq`, `deepseek`,
`xai`, `mistral`, `together`, `ollama`, `lmstudio`, plus any OpenAI-compatible
endpoint via `model.base_url` and `custom_providers` in config.

Auth resolution per provider: explicit `base_url` → API key from the environment →
OAuth login (if usable for model requests). API keys intentionally win when both
exist because some OAuth tokens are identity-only. Inspect it with `aegis auth status`.

OAuth is implemented generically (PKCE S256, `client_secret` support,
localhost-callback **and** manual-paste flows, automatic refresh, `auth.json` at
chmod 0600, token quarantine on failure). **Anthropic, OpenAI (ChatGPT/Codex
login), and Google (Gemini login)** ship with working OAuth configs:

```bash
aegis auth login anthropic     # browser → paste code
aegis auth login openai        # ChatGPT login, localhost:1455 callback
aegis auth login google        # Google sign-in, loopback callback
```

OpenAI login + token storage/refresh can succeed while the token lacks the
`model.request` scope needed for inference; AEGIS detects that and falls back to
API-key auth when available. API keys remain the reliable OpenAI path. Any other
IdP wires up by overriding `OAuthConfig`.

## Tools & permissions

Built-ins: `read_file`, `write_file`, `edit_file`, `list_dir`, `glob`, `search`,
`bash`, `web_fetch`, `web_search`, `todo_write`, `memory`, `skill`,
`spawn_subagent`, `generate_image`, `execute_code` (RPC sandbox), `browser`
(Playwright), `computer` (pyautogui), plus every connected **MCP** tool
(`mcp__<server>__<tool>`) and any plugin tools.

Every tool with a danger group (`fs`, `runtime`, `network`) flows through a
permission cascade: `deny_groups` → exec mode (`deny | allowlist | ask | auto |
full`) → allowlist prefixes → interactive approval. Read-only tools are always
allowed. Set the policy with `aegis config set tools.exec_mode ask` (or pass
`--yolo` to auto-approve a session).

## Memory & skills

* **Memory** — `~/.aegis/memories/{MEMORY.md,USER.md}` (`§`-delimited, char-capped,
  atomic writes) plus an append-only `history.jsonl`. The agent persists facts via
  the `memory` tool; a frozen snapshot keeps the system prompt cache-stable.
  Pluggable external backends: set `memory.provider` to `honcho` (`pip install
  honcho-ai`), `mem0` (`pip install mem0ai`), or `jsonl` (zero-dep) — they layer on
  top of the always-on file memory.
* **Skills** — `SKILL.md` packages (agentskills.io-compatible frontmatter) loaded
  with progressive disclosure and tiered precedence (workspace > personal >
  configured > bundled). `aegis skills new <name>` scaffolds one;
  `requires.{env,bins,os}` gates availability.

## Identity & rules

Drop these into `~/.aegis/workspace/` (global) or your project root (local, wins):

* `SOUL.md` — persona / tone
* `AGENTS.md` (or `.aegis.md` / `CLAUDE.md`) — operational rules
* `USER.md` — facts about you

## MCP (Model Context Protocol)

Connect any MCP server (stdio or Streamable HTTP); their tools appear to the agent
as `mcp__<server>__<tool>` and flow through the same permission cascade.

```bash
aegis mcp add filesystem "npx -y @modelcontextprotocol/server-filesystem /tmp"
aegis mcp test            # connect + list tools for each server
aegis mcp list
```

Also reads a Claude-Desktop-format `~/.aegis/mcp.json` (`{"mcpServers": {...}}`).

## Channels (gateway)

One agent, many surfaces: `cli`, `telegram` (core), `discord`, `slack` (extras).

```bash
export TELEGRAM_BOT_TOKEN=...                 # or DISCORD_BOT_TOKEN / SLACK_*_TOKEN
aegis gateway --channels telegram,discord,slack
```

Per-conversation sessions, control commands (`/new`, `/status`), and an optional
cron ticker.

## Skill & tool marketplace

```bash
aegis skills search pdf                        # query the agentskills.io registry
aegis skills install git:owner/repo            # clone a repo of SKILL.md packages
aegis skills install git:owner/repo@main/skills/foo   # a subdir at a ref
aegis skills install ./local/skill-dir         # a local package
aegis skills remove foo
```

Installs are tracked in `~/.aegis/skills/.lock.json` (source + SHA-256 digest).

## execute_code (zero-context-cost turns)

The agent can write a Python script that orchestrates many tool calls; the child
process reaches tools over a Unix socket and **only its stdout returns** to the
model — collapsing multi-step pipelines into one cheap turn. Secrets are stripped
from the child env.

## Serve as an OpenAI-compatible API

```bash
aegis serve --port 8790      # POST /v1/chat/completions, GET /v1/models
```

Point any OpenAI client at it; AEGIS (tools, memory, skills) runs behind the API.

## Cron / scheduled tasks

```bash
aegis cron add "@daily" "summarize today's git commits and email me"
aegis cron add "30m" "check CI and report failures"
aegis cron run               # start the scheduler (or it ticks inside the gateway)
```

## Plugins

Drop a `*.py` into `~/.aegis/plugins/` exporting `register(api)` to add tools,
channels, or providers with no core edits.

## The agent loop

Bounded synchronous loop (`max_iterations`, default 50) with a final grace call
for a summary; three-tier system prompt (stable / context / volatile) rebuilt only
on compaction; message governance (orphan-drop + backfill) before every call;
concurrent tool execution (≤8 workers); LLM compaction preserving the first 3 and
last 20 turns.

## Layout

```
aegis/
  providers/   transports (chat_completions, anthropic) + auth (key, OAuth)
  tools/       base, permissions, registry, builtin
  agent/       context, governance, compaction, loop, agent
  memory.py    skills.py    session.py (SQLite)
  gateway/     runner + channels (cli, telegram)
  cli/         main (subcommands) + repl (TUI)
  builtin_skills/web-research/SKILL.md
tests/test_smoke.py
```

## Commands

`aegis [chat|model|auth|setup|onboard|update|completion|skills|mcp|serve|cron|tools|memory|config|sessions|gateway|doctor|backup|import|insights|webhook|hooks|kanban|curator|dashboard|acp|pairing|checkpoints|background]`
— run any with `-h`. `aegis` alone opens the REPL. `chat` flags: `--resume`,
`--continue`, `--worktree/-w`, `--yolo`, `--model`, `--provider`, `--image`.
Slash commands in the REPL: `/usage /compress /background /rollback /personality
/model /tools /skills /memory /sessions /new`.

## Rename it

Everything keys off `APP_NAME`/the package name. To rebrand: rename the `aegis/`
package dir, update `pyproject.toml` (`name`, `[project.scripts]`), and the
`APP_NAME` constants. The runtime home is `$AEGIS_HOME` or `~/.aegis`.

## Test

```bash
pip install -e ".[dev]"
pytest -q        # runs fully offline against a fake provider
```

## License

MIT.
