# Hermes vs AEGIS Full Audit

Date: 2026-06-13

Reference sources:
- Hermes public repo: https://github.com/NousResearch/hermes-agent
- Hermes docs: https://hermes-agent.nousresearch.com/docs/
- Hermes web dashboard docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard
- Hermes memory docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- Hermes memory provider docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers
- Hermes messaging docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging
- Hermes cron docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/cron
- Hermes providers docs: https://hermes-agent.nousresearch.com/docs/integrations/providers

Local refs audited:
- AEGIS base: `ad803e5b28be4da9f2b2f7baebbc0d39c66bc158`
- Hermes origin/main: `8cf9d8689d56dc8ad742a6113b0f502ec464c835`

Implementation update in this branch:
- Added Hermes-style runtime guidance to call `session_search` before answering prior-session questions.
- Expanded AEGIS `session_search` to browse, discover, read, and scroll past sessions.
- Added external-memory `on_memory_write` mirroring hook and JSONL provider support.
- Added typed FastAPI control-plane routes for auth status, health, config defaults/schema/raw/import, env list/set/reveal/delete, and sessions list/search/stats/detail/export/prune/delete.
- Added regression tests for recall behavior and memory write mirroring.

## Executive Verdict

AEGIS is not empty or toy-level anymore. It already has the core shape of a Hermes-like agent: persistent sessions, file/user memory, skills, background learning, FastAPI dashboard, PTY-over-WebSocket terminal, gateway channels, cron, MCP client/server, ACP, providers, tracing, evals, LSP, process tools, cloud/tool backends, and a usable React dashboard.

The gaps are mostly depth and integration. Hermes is a mature product with a very wide control plane: a 169-route FastAPI dashboard server, OAuth-gated dashboard auth, profile-aware management, rich session search, 8 first-class external memory provider plugins, 20+ messaging platforms, a typed cron/job system with blueprints, a larger toolset registry, first-class provider/credential routing, and mature plugin/hub workflows.

Most urgent issue from the conversation: AEGIS has session search storage, but the agent prompt does not force recall when the user asks "what did we talk about last time?" Hermes explicitly injects that rule. That is why AEGIS answered from live context first instead of searching memory/history.

## Critical Gaps

### 1. Cross-session recall behavior

Status: implemented for local AEGIS sessions in this branch.

AEGIS has:
- `aegis/session.py`: SQLite-backed sessions with FTS5 message snippets.
- `aegis/tools/recall.py`: `session_search(query, limit)` tool.
- Memory files: `MEMORY.md` and `USER.md`, frozen snapshots, limits, injection scanning.

Hermes has:
- System prompt guidance: when the user references past conversation or relevant cross-session context may exist, use `session_search` before asking them to repeat.
- `session_search()` with four shapes: browse recent sessions with no query, discover by query with bookends and anchored message windows, read by `session_id`, and scroll around `around_message_id`.
- Cross-profile session reads.

Implemented:
- Added Hermes-style `session_search` prompt guidance when the tool is available.
- Expanded the schema so `query` is optional and supports no-arg browse, `session_id` read, `around_message_id`/`window` scroll, `role_filter`, `sort`, and Hermes-compatible `profile`.
- Returned structured JSON with message IDs, bookends, anchored windows, and current-lineage exclusion.
- Added tests for prompt guidance plus browse/discover/read/scroll.

Still missing:
- Cross-profile session search. AEGIS currently warns and ignores `profile` because local sessions are not profile-partitioned yet.
- A normalized message table. Anchors use saved message index IDs, which are reliable for loaded AEGIS transcripts but not as strong as Hermes' dedicated message rows.

### 2. Dashboard backend is FastAPI, but not Hermes-grade

Status: improved, still partial.

AEGIS has:
- `aegis/dashboard_fastapi.py`: FastAPI app, static SPA serving, token/cookie auth, `/api/ws`, `/api/pty`, upload, chat stream, and generic `/api/{path}` dispatcher.
- 33 FastAPI route decorators after this branch's typed control-plane route pass, plus compatibility fallback through 36 explicit API dispatcher paths.
- `web/src/pages/TerminalPage.tsx`: xterm.js terminal to `/api/pty`.

Hermes has:
- `hermes_cli/web_server.py`: 169 route decorators.
- Dedicated routes for media, files, fs read/data URLs, status, system stats, curator, portal, ops, self-update, audio, actions, sessions, profiles, config/defaults/schema/raw, models, env vars/reveal, provider OAuth, messaging onboarding, cron jobs/runs/blueprints, MCP servers/catalog, pairing, webhooks, gateway start/stop/restart, credential pools, memory provider, doctor/security/backup/import/hooks/checkpoints, skills hub/search/scan/preview, tools/toolsets, analytics, dashboard themes/fonts/plugins, and multiple WebSockets.
- `dashboard_auth/` with login, OAuth/cookie auth, auth providers, logout, `/api/auth/me`, and WS tickets.
- Profile-aware dashboard management and remote dashboard support.

Implemented:
- Added typed FastAPI routes for `/api/health`, `/api/auth/me`, config defaults/schema/raw/import, env list/set/reveal/delete, and sessions list/search/stats/detail/export/prune/delete.
- Kept the generic dispatcher as a compatibility shim for the existing dashboard app.
- Added FastAPI regression tests for route registration, config/env safety, session search/detail/export/delete, and auth status.

Needed inputs:
- Continue replacing the generic dispatcher with typed FastAPI routes for remaining parity-critical surfaces.
- Add dashboard auth middleware/gate:
  - loopback no-op mode
  - non-loopback fail-closed auth
  - login page
  - username/password or OAuth providers
  - WS ticket flow for `/api/ws` and `/api/pty`
- Add missing route groups:
  - config export
  - provider OAuth start/submit/poll/revoke
  - sessions patch/rename/message-level APIs
  - cron jobs/runs/blueprints
  - gateway start/stop/restart/status
  - audio transcribe/TTS/voices
  - self-update check/apply
  - dashboard themes/fonts/plugins
  - ops doctor/security-audit/backup/import/migrate/dump
- Make profile selection a first-class URL-scoped dashboard concept.

### 3. External memory providers are thin

Status: partial.

AEGIS has:
- Built-in memory close to Hermes semantics.
- Provider interface with lifecycle hooks.
- `jsonl`, `mem0`, `honcho`, and generic HTTP providers for openviking/supermemory/byterover/hindsight/holographic/retaindb.

Hermes has:
- 8 first-class provider plugins: Honcho, OpenViking, Mem0, Hindsight, Holographic, RetainDB, ByteRover, Supermemory.
- Provider setup wizards, config schemas, provider-specific tools, prefetch/queue_prefetch, session switching, delegation observations, compression hooks, and mirroring of built-in memory writes.

Implemented:
- Added fail-soft `on_memory_write` to the provider lifecycle.
- Mirrored successful memory add/replace/remove calls to external providers.
- Added JSONL provider mirroring so direct memory-tool writes appear in external memory history.
- Added regression tests for lifecycle fan-out and `handle_tool` mirroring.

Needed inputs:
- Convert AEGIS generic HTTP adapters into plugin-quality providers with config schemas and setup commands.
- Add provider-specific tools where useful.
- Add provider activation UI/CLI parity.
- Add per-provider tests with fake SDK/server clients.

### 4. Messaging gateway breadth and polish

Status: partial.

AEGIS has:
- CLI, Telegram, Discord, Slack, Signal, Matrix, Email, Webhook, ntfy.
- Pairing, per-channel sessions, interrupt/steer, model/provider/reasoning commands, delivery queue.

Hermes has:
- 20+ platforms: Telegram, Discord, Slack, WhatsApp, Signal, SMS, Email, Home Assistant, Mattermost, Matrix, DingTalk, Feishu/Lark, WeCom, Weixin, BlueBubbles/iMessage, QQ, Yuanbao, Teams, LINE, ntfy, browser/Open WebUI/API.
- Rich platform behavior: native files/images/audio, voice message transcription, thread support, typing indicators, streaming edits, slash commands, app command registration, channel directories, cross-platform delivery, gateway hooks, and per-platform authz rules.

Needed inputs:
- Add priority missing platforms based on user need: WhatsApp, Google Chat/Teams, Home Assistant, SMS, Mattermost, Feishu/Lark.
- Add voice/media parity per existing platforms before adding too many new ones.
- Add channel directory and home-channel resolution for cron/delivery.
- Add native slash/app command surfaces where platforms support them.
- Make gateway process status and restart fully dashboard-managed.

### 5. Cron automation is useful but not Hermes-grade

Status: partial.

AEGIS has:
- JSON-backed cron jobs, interval/5-field/one-shot schedules, script context, skill preload, delivery targets, `[SILENT]`, direct or queued delivery.

Hermes has:
- Unified `cronjob` tool with action-style operations.
- Typed job store, next-run tracking, missed-run recovery, file lock, scheduler state, run logs, multiple delivery targets, origin delivery, no-agent script mode, per-job toolsets, blueprints, dashboard job runs, and stronger prompt-injection blocking.

Needed inputs:
- Add `cronjob` tool compatible with Hermes action schema.
- Switch jobs to a structured store with `state`, `next_run_at`, `last_run_at`, `last_error`, `runs`, and `delivery_error`.
- Add file/process lock around tick.
- Add cron blueprints and dashboard create/edit flow.
- Add script-only/no-agent mode.
- Disable recursive cron creation inside cron sessions.

### 6. Skills and learning loop need Hermes tool parity

Status: fairly strong, but missing mature tooling.

AEGIS has:
- 32 bundled skills.
- Progressive SKILL.md disclosure.
- `skill` tool, `learn review/list/apply/reject`, background review on by default, auto memory/skill application, curator review/prune/archive/restore.

Hermes has:
- Larger bundled/optional skill ecosystem and hub.
- `skill_view`, `skills_list`, `skill_manage` with create/patch/write_file/delete flows.
- Stronger background review and curator: snapshots, pin/unpin, rollback, reports, safe consolidation/pruning, cron reference rewrites, auxiliary model routing.

Needed inputs:
- Add Hermes-compatible `skill_manage` action schema.
- Add support files under skill dirs (`references/`, `scripts/`, templates) through the tool.
- Add curator run reports, backups, rollback, pin/unpin CLI and dashboard.
- Add skill hub scan/preview/update/install parity.
- Add skill nudge text to prompt, not only terminal UI event.

### 7. Provider/auth/routing gaps

Status: partial.

AEGIS has:
- 27 provider specs.
- OAuth for Anthropic, OpenAI, OpenAI Codex, Google.
- Custom providers, fallback providers, routing list, plugin provider registration.

Hermes has:
- Larger provider catalog and setup flows: Nous Portal, OpenAI Codex, GitHub Copilot, Anthropic, OpenRouter, xAI OAuth, Qwen OAuth, MiniMax OAuth, Bedrock, Azure Foundry, Ollama Cloud, many regional providers.
- Nous Portal and Tool Gateway: one OAuth for models plus web search/image/TTS/browser backends.
- Credential pools: multiple credentials per provider, rotation, exhaustion/cooldown, refresh, concurrent leasing for subagents.
- OpenRouter provider routing controls.

Needed inputs:
- Add Nous Portal provider and Tool Gateway routing if AEGIS wants Hermes-like "one login" UX.
- Add credential pool storage and provider resolution.
- Add OAuth flows for Qwen, MiniMax, xAI, Copilot, and platform-specific providers as needed.
- Add dashboard/CLI provider setup wizard parity.
- Add OpenRouter provider routing config through provider transports.

### 8. Tool surface and toolsets are smaller

Status: partial.

AEGIS has 37 built-in tools:
`agent_state`, `apply_patch`, `bash`, `browser`, `clarify`, `cloud_browser`, `cloud_image`, `computer`, `dependency_audit`, `download`, `edit_file`, `execute_code`, `generate_image`, `github`, `glob`, `http_request`, `kanban`, `list_dir`, `lsp`, `memory`, `mixture_of_agents`, `process`, `read_file`, `schedule_task`, `search`, `send_message`, `session_search`, `skill`, `spawn_subagent`, `speak`, `system_status`, `todo_write`, `tool_search`, `transcribe`, `web_fetch`, `web_search`, `write_file`.

Hermes has a larger registry and explicit toolsets including browser, clarify, code_execution, cronjob, delegation, file, image, kanban, memory, messaging, MCP, MoA, process registry, safe, search, session_search, skills, terminal, todo, TTS, video, vision, web, X search, and platform tools.

Needed inputs:
- Add Hermes-compatible names/aliases for core tools where behavior matches.
- Expand `tool_search` deferred-schema behavior to match Hermes semantics.
- Add missing high-value tools: vision analyze, richer browser actions, video generation, X search, process registry parity, toolset config introspection.
- Add explicit toolset registry with dashboard config.

### 9. MCP and ACP are present but thinner

Status: partial.

AEGIS has:
- MCP client over stdio/HTTP.
- MCP server exposing AEGIS tools.
- ACP adapter.

Hermes has:
- MCP catalog, install/test UI, per-session ACP MCP registration, ACP session load/resume/list with history replay, permission bridging, edit approval bridging, context usage, model picker state, provenance metadata, and registry integration.

Needed inputs:
- Add MCP catalog parity with install/test/env prompting.
- Add ACP history replay and session list/detail parity.
- Add ACP edit approvals and permission option mapping.
- Add context-usage updates and model selector state.

### 10. Desktop, install, update, ops

Status: partial.

AEGIS has:
- Desktop app skeleton, install scripts, daemon/systemd helpers, doctor, backup, checkpoints, dashboard.

Hermes has:
- Native Windows docs/support, GUI uninstall, update check/apply, dashboard remote backend support, service managers, logs/status commands, config migration, prompt-size/dump/debug-share operations.

Needed inputs:
- Add self-update check/apply routes and CLI.
- Add config migration route/tool.
- Add richer doctor/security-audit/backup/import ops through dashboard.
- Add remote dashboard auth hardening.
- Add Windows-native parity only if it matters for target users.

## Priority Build Plan

P0: Fix recall behavior
- Done in this branch: `SESSION_SEARCH_GUIDANCE`-style prompt guidance, browse/discover/read/scroll, and regression tests.

P1: Make the dashboard a real control plane
- Done in this branch: typed routes for auth status, health, config, env, and sessions while keeping old paths as compatibility shims.
- Still needed: auth middleware + WS tickets, provider OAuth, cron, gateway, audio, self-update, ops, and profile-aware management.

P2: Cron and gateway foundations
- Structured cron store and `cronjob` tool.
- Gateway status/start/stop/restart in dashboard.
- Channel directory and home-target resolution.

P3: Memory provider parity
- Implement first-class Honcho provider with config/setup/tools.
- Then Mem0 and Supermemory.
- Done in this branch: memory-write mirroring hook and tests.
- Still needed: per-provider setup/tools/prefetch tests.

P4: Provider/account UX
- Nous Portal/Tool Gateway if desired.
- Credential pools.
- OAuth setup UI.

P5: Skills/curator polish
- `skill_manage` schema.
- Skill backups/rollback/reports/pin.
- Hub scan/preview/update parity.

## Bottom Line

AEGIS is close in architecture but not yet close in product surface. The most important immediate fix is not another big subsystem: it is making the agent use the session memory it already has. After that, the biggest product gap is the dashboard backend. Hermes is not merely serving React with FastAPI; it is running a full local operating console for the agent.
