# AEGIS

A self-improving, multi-provider, multi-channel **terminal agent harness** in Python:
open, self-hostable, and built around one auditable local runtime.

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
aegis              # chat after onboarding
```

## What you get

- **Any model** — 29 provider presets (Anthropic, OpenAI, Google, OpenRouter, Groq,
  DeepSeek, Qwen, MiniMax, xAI, Mistral, Together, Ollama, …) with **API-key *and* OAuth** auth,
  fallback chains, credential pools, and per-prompt routing.
- **Tools** — 45 registered tools for files, shell, web, browser/computer-use,
  image, voice, `execute_code`, subagents, LSP, process management, GitHub,
  shared agent state, MCP, plugins, and a capability-gated permission cascade.
- **Memory & a closed learning loop** — file memory + Honcho/Mem0/JSONL/HTTP backends,
  FTS5 session recall, and `aegis learn` (review → extract → redact → approve → promote).
- **Skills** — 41 bundled `SKILL.md` packages, progressive disclosure, a marketplace
  (`aegis skills hub`), and self-improvement (`skill create/improve`).
- **Cockpit and observability** — dashboard traces/runs/agents pages, local span
  storage, and provider-free replay evals with `aegis trace` and `aegis eval`.
- **Python SDK** — embed the agent loop directly with session continuity, progress
  events, trace lookup, branching, and eval replay.
- **Gateway** — Telegram, Discord, Slack, Signal, Matrix, Email, webhooks, and ntfy;
  DM pairing, platform hints, and a durable delivery queue with retries.
- **Sandboxing** — local / Docker / SSH / Singularity / Modal backends, fail-closed.
- **Serve** — expose AEGIS as an OpenAI-compatible API or an **MCP server**.

See the repository README for the full public feature reference.
