# AEGIS

A self-improving, multi-provider, multi-channel **terminal agent harness** in Python —
an open, self-hostable replacement for Hermes Agent and OpenClaw, in ~10k auditable lines.

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
aegis setup        # pick a provider + key
aegis              # chat
```

## What you get

- **Any model** — 26 provider presets (Anthropic, OpenAI, Google, OpenRouter, Groq,
  DeepSeek, Mistral, xAI, Together, Ollama, …) with **API-key *and* OAuth** auth,
  fallback chains, credential pools, and per-prompt routing.
- **Tools** — fs, shell, web (multi-backend search), browser, computer-use, image,
  voice (STT/TTS), `execute_code` RPC, subagents, LSP, process management, GitHub,
  MCP tools, and a capability-gated permission cascade with a hardline blocklist.
- **Memory & a closed learning loop** — file memory + Honcho/Mem0/JSONL/HTTP backends,
  FTS5 session recall, and `aegis learn` (review → extract → redact → approve → promote).
- **Skills** — 24 bundled `SKILL.md` skills, progressive disclosure, a marketplace
  (`aegis skills hub`), and self-improvement (`skill create/improve`).
- **Gateway** — CLI, Telegram, Discord, Slack, Signal, Matrix, Email, and a webhook
  bridge; voice-memo transcription, DM pairing, a durable delivery queue with retries.
- **Sandboxing** — local / Docker / SSH / Singularity / Modal backends, fail-closed.
- **Serve** — expose AEGIS as an OpenAI-compatible API or an **MCP server**.

See the [Comparison](https://github.com/Alien0013/aegis/blob/main/COMPARISON.md) for the
full feature matrix vs Hermes and OpenClaw.
