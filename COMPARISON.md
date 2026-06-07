# AEGIS vs Hermes Agent vs OpenClaw

A feature-by-feature comparison showing AEGIS as a drop-in replacement for both.
✅ = present · ➖ = partial / via plugin · ❌ = not present.

| Capability | Hermes Agent | OpenClaw | **AEGIS** |
|---|:---:|:---:|:---:|
| **Install** | one-line `curl\|bash` | `npm i -g openclaw` | **one-line `curl\|bash`** ✅ |
| Language / runtime | Python | Node | Python |
| Bounded agent loop + grace call | ✅ | ✅ | ✅ |
| 3-tier cache-stable prompt | ✅ | ➖ | ✅ |
| Context compaction | ✅ | ✅ | ✅ |
| **Providers** (any LLM) | ✅ | ✅ | ✅ (12 + custom) |
| API-key auth | ✅ | ✅ | ✅ |
| **OAuth login** (Claude/ChatGPT/Gemini) | ✅ | ➖ | ✅ (anthropic/openai/google) |
| Local models (Ollama/LM Studio/vLLM) | ✅ | ✅ | ✅ |
| Fallback provider chains | ✅ | ➖ | ✅ |
| **Tools** (fs/shell/web) | ✅ | ✅ | ✅ |
| Permission modes / approval | ✅ | ✅ | ✅ (deny/allowlist/ask/auto/full) |
| `execute_code` RPC (zero-context-cost) | ✅ | ➖ | ✅ |
| Subagents | ✅ | ✅ | ✅ |
| Image generation | ✅ | ➖ | ✅ |
| **Browser / computer-use** | ✅ | ✅ | ✅ (Playwright + pyautogui) |
| **MCP client** (stdio + HTTP) | ✅ | ✅ | ✅ |
| **Persistent memory** | ✅ | ✅ | ✅ (MEMORY.md/USER.md + history) |
| **Skills** (SKILL.md) | ✅ | ✅ | ✅ |
| **Auto-skill-generation** (self-improving) | ✅ | ➖ | ✅ (`skill create`) |
| **Skill/tool marketplace** (install/search) | ✅ | ✅ | ✅ (git/url/local + agentskills.io) |
| **Multi-channel gateway** | ✅ | ✅ | ✅ |
| Channels: CLI / Telegram | ✅ | ✅ | ✅ |
| Channels: Discord / Slack | ✅ | ✅ | ✅ |
| Channels: WhatsApp / Signal / Matrix | ✅ | ✅ | ➖ (plugin-able) |
| **Voice** (STT / TTS) | ✅ | ➖ | ✅ (provider audio API) |
| **Cron / scheduled tasks** | ✅ | ✅ | ✅ |
| **Serve** (OpenAI-compatible API) | ✅ | ➖ | ✅ |
| Sessions / resume / continue | ✅ | ✅ | ✅ (SQLite) |
| Git worktree mode | ✅ | ➖ | ✅ |
| Personalities | ✅ | ➖ | ✅ |
| Profiles | ✅ | ✅ | ✅ |
| SOUL.md / AGENTS.md identity | ➖ | ✅ | ✅ |
| GitHub integration | ➖ | ✅ | ✅ (skill) |
| Coding-agent delegation (claude/codex/opencode) | ➖ | ✅ | ✅ (skill) |
| Plugins (drop-in tools/channels/providers) | ✅ | ✅ | ✅ |
| Self-update (`update`) | ✅ | ✅ | ✅ |
| TUI (streaming, slash commands) | ✅ | ➖ | ✅ |
| Setup wizard / onboard | ✅ | ✅ | ✅ |

## Power-user parity (added)

Originally-missing Hermes features now implemented in AEGIS:

| Feature | Status |
|---|---|
| Smart approval mode (auxiliary-LLM risk assessment) | ✅ `exec_mode: smart` |
| Hardline blocklist (catastrophic cmds refused even in yolo) | ✅ |
| Tirith-style pre-execution security scan | ✅ `security_scan` |
| Anthropic prompt caching | ✅ |
| Credential pools (rotate keys on 429/401) | ✅ |
| Auxiliary (small) model for compaction/vision/approval | ✅ `auxiliary.*` |
| Sandboxed terminal backends (local/docker/ssh) | ✅ `tools.terminal_backend` |
| ACP / IDE integration | ✅ `aegis acp` |
| Pluggable external memory (Mem0 / JSONL) | ✅ `memory.provider` |
| Vision (image input) | ✅ `chat --image` |
| Shadow-file checkpoints + rollback | ✅ `aegis checkpoints`, `/rollback` |
| DM pairing / gateway authorization | ✅ `aegis pairing` |
| Webhooks (event → agent) | ✅ `aegis webhook` |
| Lifecycle hooks (shell scripts) | ✅ `aegis hooks`, config `hooks.*` |
| Signal + Matrix channels | ✅ (WhatsApp via plugin) |
| Kanban multi-agent board | ✅ `aegis kanban` |
| Skill curator (background maintenance) | ✅ `aegis curator` |
| Web dashboard | ✅ `aegis dashboard` |
| Usage insights / analytics | ✅ `aegis insights` |
| Backup / restore | ✅ `aegis backup` / `import` |
| Background tasks | ✅ `/background`, `aegis background` |
| `@file` references, `/usage`, `/compress`, status bar | ✅ |
| 26 provider presets (+ custom) | ✅ |
| Self-update, shell completion, doctor --fix | ✅ |

## Where AEGIS intentionally differs

* **Lean core (~6k LOC, 45 modules).** Like NanoClaw, the whole engine is readable
  in an afternoon. OpenClaw is ~434k LOC; Hermes ships 64 tools — AEGIS keeps a
  tight built-in set and pushes breadth to MCP, the marketplace, and plugins.
* **Heavy features are opt-in extras**, not core weight: `[browser]`, `[computer]`,
  `[discord]`, `[slack]`. The core install stays small and fast.
* **One provider abstraction, two transports.** New wire protocol = new transport
  class, never if/elif sprawl.

## The one honest gap

OpenAI/Google **OAuth login + token storage/refresh work**, but whether those
bearer tokens authorize *inference* depends on the scopes those providers grant
third-party clients (and their Codex/Code-Assist backends). API keys are the
always-reliable path for all providers; Anthropic OAuth is the most complete.
This is an upstream provider constraint, not an AEGIS limitation.

Signal (via `signal-cli`) and Matrix (via `matrix-nio`) channels are now built in;
WhatsApp still needs a paid business API or bridge and slots in via the plugin
channel API. The remaining true gaps vs Hermes are ecosystem/proprietary: Nous
Portal (Nous-only subscription backend), the desktop GUI app, and the curated
MCP/skill registries — none of which are core agent capability.
