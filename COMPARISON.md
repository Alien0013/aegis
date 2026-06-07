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

WhatsApp/Signal/Matrix channels aren't bundled (they need paid business APIs or
external bridges like `signal-cli`) but slot in via the plugin channel API.
