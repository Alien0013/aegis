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

## Entry surfaces (`surface.py`)
`SurfaceRunner` is the shared factory for terminal and service surfaces: REPL
turns, one-shot CLI prompts, TUI, batch runs, Python SDK, OpenAI-compatible
`serve`, JSON-RPC stdio (`aegis rpc`), ACP/IDE sessions, dashboard chat, cron,
webhooks, and background jobs. It standardizes
`SessionStore`, working directory, MCP loading, platform/chat metadata, event
callbacks, and trace/session metadata before the request reaches `Agent.run`.

Terminal surfaces also share a small runtime layer for user controls: `/goal`
and automatic continuations, `/retry`, and manual `/compress` flow through the
same runner/run-log/trace path instead of bypassing the core loop.

Prompt context references are part of this shared surface layer. `@file:path`,
`@folder:path`, `@diff`, `@staged`, `@git:<ref>`, and `@url:https://...` expand
through `context_refs.py` for the REPL, TUI, SDK, OpenAI-compatible API, ACP,
gateway, cron, webhooks, and background jobs. Each turn records reference count,
warnings, and injected character totals in `session.meta.last_context_references`
and run metadata.

Within a runner, agents are cached by session/model/provider/cwd/MCP/approval
identity and serialized per session. This keeps provider objects warm for
transport-level continuity, including Codex app-server threads and provider
prefix caches, while rebuilding when the model, provider, working directory, MCP
setting, or approval callback changes.

Surface sessions carry a `surface` metadata field such as `serve`, `dashboard`,
`cron`, `webhook`, or `background`, which makes automation and API runs visible
to session search, traces, dashboard replay, and eval suites.

Each surface execution also writes to `runs.db` through `RunStore`: run id,
surface, status, session id, trace id, prompt/result previews, and metadata. The
dashboard `/api/runs` and `/api/run` endpoints read this durable run log and can
join back to the linked session and trace.

## The loop (`agent/`)
`run_conversation` is a bounded synchronous loop (default 50 iterations + one grace
call). Before each model call, `governance.normalize` drops orphan tool results and
backfills missing ones. The system prompt is built once per session in three tiers
(stable / context / volatile) for prefix-cache reuse; `compaction` summarizes the
middle when the window fills.

Prompt assembly also records debug metadata on the session: full prompt hash,
token/character estimates, and named prompt parts with tier, hash, size, and
token estimates. Turn traces snapshot that metadata before the provider call, so
dashboard replay and eval replay can show which identity, project rules, skills,
memory, platform hints, and runtime sections were active for the run.

Context engines can expose tools and receive lifecycle hooks:
`on_session_start`, `on_pre_compress`, and `on_session_switch`. Compression
splits record parent `end_reason=compression`, child lineage root/depth, creator
kind, reason, and child ids so dashboard/session replay can follow continuation
chains.

## Providers (`providers/`)
`ProviderTransport` has one implementation per wire protocol (`chat_completions`,
`anthropic`). `Provider` binds a transport to an endpoint + model + `AuthProvider`
(API key **or** OAuth/PKCE). `reasoning` effort maps to Claude extended-thinking and
OpenAI `reasoning_effort`. `FallbackProvider` + credential pools handle failover.
`AuxRouter` centralizes non-user-facing model work such as compaction, session
summaries, and trajectory compression so internal tasks use the configured
`auxiliary.provider` / `auxiliary.model` consistently. Purpose-specific
`auxiliary.<purpose>.provider/model/context_length` overrides are supported; when
unset, internal work follows the live main provider selected for the current turn.

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
`session_search` gives the agent cross-session recall. `agent_state` exposes
current/session state, branching, traces, eval runs, and background work as a
model-visible tool so every surface can inspect the same runtime state.
After each turn, `session.meta` includes runtime provider/model/transport/context
length, usage/cache counters, trace/turn ids, response-state pointers when
available, prompt-part metadata, and tool-call counts.

## Gateway (`gateway/`)
Hub-and-spoke: adapters → `SessionStore` (deterministic keys, isolation modes) →
agent. DM pairing, mention gating, control commands.
