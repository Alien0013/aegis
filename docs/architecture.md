# AEGIS Architecture

A lean (~10k LOC) terminal agent harness. One bounded synchronous loop, pluggable
providers, a capability-gated tool system, persistent memory + a learning loop, a
SKILL.md skills engine, and a multi-channel gateway.

```
 channels ─▶ gateway ─▶ Agent.run ─▶ run_conversation (bounded loop)
   cli/tg/                 │              │  provider.complete (stream, reasoning)
   discord/slack/          │              │  tool_executor (ThreadPool, permissions)
   signal/matrix           │              │  compaction (preserve first 3 + last 20)
                           ▼              ▼
            ContextBuilder (3-tier)   memory · skills · checkpoints · hooks
            stable | context | volatile
```

## The loop (`agent/`)
`run_conversation` is a bounded synchronous loop (default 50 iterations + one grace
call). Before each model call, `governance.normalize` drops orphan tool results and
backfills missing ones. The system prompt is built once per session in three tiers
(stable / context / volatile) for prefix-cache reuse; `compaction` summarizes the
middle when the window fills.

## Providers (`providers/`)
`ProviderTransport` has one implementation per wire protocol (`chat_completions`,
`anthropic`). `Provider` binds a transport to an endpoint + model + `AuthProvider`
(API key **or** OAuth/PKCE). `reasoning` effort maps to Claude extended-thinking and
OpenAI `reasoning_effort`. `FallbackProvider` + credential pools handle failover.

## Tools & permissions (`tools/`)
`Tool` subclasses declare JSON-schema params, danger `groups`, and a `toolset`. The
`PermissionEngine` cascade: hardline blocklist → `deny_groups` → exec mode → allowlist
→ approval, plus a Tirith-style pre-exec scan. Terminal execution runs through
local/docker/ssh backends (fail-closed).

## Memory, skills, learning
- **Memory** — file-backed `MEMORY.md`/`USER.md` + `history.jsonl`; pluggable
  external backends (Honcho/Mem0/JSONL/HTTP).
- **Skills** — `SKILL.md` packages, progressive disclosure, tiered precedence, usage
  tracking. The agent can `create`/`improve` skills.
- **Learning loop** (`learn.py`) — reviews a session, extracts redacted memory/skill
  candidates, and promotes them after approval (`aegis learn`).

## Sessions (`session.py`)
SQLite store with FTS5 full-text search across messages, summaries, and resume.
`session_search` gives the agent cross-session recall.

## Gateway (`gateway/`)
Hub-and-spoke: adapters → `SessionStore` (deterministic keys, isolation modes) →
agent. DM pairing, mention gating, control commands.

