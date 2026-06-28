# AEGIS Full-Agent Maturity Matrix

AEGIS is tracked as a complete local-first agent system, not only a chat command. This matrix records local evidence for the same operating class across runtime loop, prompt context, tool registry, terminal processes, memory layers, session recovery, skills lifecycle, gateway adapter coverage, cron semantics, delegation, provider routing, CLI/TUI, desktop dashboard, security approvals, and the extension ladder.

The matrix is intentionally native to AEGIS. It does not copy another product's naming into the product surface. It also separates local automated proof from live third-party or cross-OS proof.

## How to check it

```bash
aegis maturity --check
aegis maturity --json --check
```

The dashboard exposes the same payload at `/api/maturity`; live validation targets are available at `/api/live-qa`.

## Layer matrix

| Layer | Local evidence | Maturity contract |
| --- | --- | --- |
| runtime loop | `aegis/agent/agent.py`, `aegis/agent/loop.py`, tests under `tests/test_agent*` | Bounded model loop, continuation, compaction, tool dispatch, provider errors, and role hygiene. |
| prompt context | `aegis/agent/context.py`, `aegis/context_refs.py`, `aegis/agent/compaction.py` | Stable/context/volatile prompt context, project references, prompt context limits, anti-injection scanning, and compression metadata. |
| tool registry | `aegis/tools/registry.py`, generated `docs/tools-reference.md` | Tool schema registry, toolsets, risk metadata, requirement gating, schema portability, and generated reference drift checks. |
| terminal processes | `aegis/tools/process.py`, `aegis/tools/process_registry.py`, `aegis/tools/backends.py` | Foreground command execution, tracked background processes, process lifecycle controls, backend validation, and permission gates. |
| memory layers | `aegis/memory.py`, `aegis/memory_providers.py`, `aegis/tools/recall.py` | Durable user facts, operator notes, session recall, external memory providers, and skills stay distinct. |
| session recovery | `aegis/session.py`, `aegis/session_checks.py`, `aegis/runs.py` | SQLite session recovery, FTS/search, lineage, run/trace breadcrumbs, export, and dashboard replay. |
| skills lifecycle | `aegis/skills.py`, `aegis/curator.py`, `aegis/tools/skill_manage.py` | SKILL.md packages, skill usage, curation, archive/pin behavior, self-learning candidates, and approval boundaries. |
| gateway adapter | `aegis/gateway/runner.py`, `aegis/gateway/base.py`, `aegis/platforms/helpers.py` | Long-running service, pairing, allowlists, per-channel sessions, attachments, outbox, fake adapters, and credentialed live QA separation. |
| cron semantics | `aegis/cron.py`, `aegis/tools/cronjob_tool.py`, `aegis/dashboard_routes/cron_jobs.py` | Schedules, dry-runs, prompts, scripts, delivery, dashboard preview, and background job visibility. |
| delegation | `aegis/tools/agentic.py`, `aegis/background.py`, `aegis/kanban_auto.py` | Isolated subagent/task orchestration, background summaries, parent verification, and kanban work queues. |
| provider routing | `aegis/providers/registry.py`, `aegis/providers/auth.py`, `aegis/providers/fallback.py` | API-key/OAuth auth, credential pools, fallback chains, auxiliary routing, and redacted provider status. |
| CLI and TUI | `aegis/cli/main.py`, `aegis/cli/repl.py`, `aegis/cli/tui.py` | CLI parser, slash commands, help output, generated references, terminal/TUI aliases, and gateway-compatible command behavior. |
| desktop dashboard | `aegis/dashboard_fastapi.py`, `web/`, `desktop/` | FastAPI dashboard, React/Vite UI, Electron shell, setup readiness, token-safe WebSockets, update state, and release smoke checks. |
| security approvals | `aegis/tools/permissions.py`, `aegis/redact.py`, `aegis/tools/file_safety.py` | Command approval policy, redaction, dashboard token minimization, WebSocket tickets, file safety, and policy simulation. |
| extension ladder | `aegis/plugins.py`, `aegis/dashboard_routes/tools_mcp.py`, `aegis/webhook.py` | Existing primitives first, then CLI/docs, skills, service-gated tools, plugins, MCP/webhooks, and only then new model-visible tools. |

## Remaining gap buckets now tracked

1. Public documentation coverage is tracked by `docs/index.md`, this matrix, and the user-guide pages under `docs/user-guide/`.
2. User-guide topic coverage is represented by configuration, messaging, cron, sessions, browser, TTS, environment variables, Docker, hooks, and profile distribution pages.
3. Integration and plugin documentation is represented by the operations contract and live QA matrix.
4. Operations contracts are represented by `docs/operations-contracts.md`.
5. External live QA is represented by `docs/live-qa-matrix.md` and the `/api/live-qa` dashboard route.
6. File-family depth is represented by the source-path/local-proof rows in `aegis maturity --json`.

## Runtime loop contract

The runtime loop builds or restores prompt state, calls the selected provider, dispatches tool calls through the permission path, appends tool results in valid order, compresses only when needed, and records run/session metadata. AEGIS keeps this narrow core stable and moves optional capability to the edge.

## Prompt context contract

Prompt context is layered so stable identity and safety material can be reused while volatile user turn material changes. Project references and prompt context files are bounded, scanned, and recorded in session metadata. Compression should preserve first/last context and lineage rather than silently dropping task state.

## Tool registry contract

Tools declare schemas, toolsets, groups, risk metadata, and descriptions. Tool output hygiene matters as much as tool names: unsafe output should be redacted, large output should be bounded, and unavailable integrations should fail soft or hide behind readiness checks.

## Terminal processes contract

Terminal and process tools must distinguish foreground runs, long-lived background processes, lifecycle polling, stdin writes, and cancellation. Approval and backend validation stay on the execution path rather than in UI-only code.

## Memory layers contract

Memory layers are not interchangeable. User profile facts, operator environment notes, session history, external semantic providers, and procedural skills are separate stores with separate update rules.

## Session recovery contract

Crash recovery is a product feature. Sessions, runs, traces, summaries, lineage, and search are persisted so a later session can continue work without forcing the user to repeat context.

## Skills lifecycle contract

Skills are reusable procedures with metadata and optional references/templates/scripts. Curation and self-learning must preserve approvals, usage tracking, archive safety, and pin protections.

## Gateway adapter contract

Gateway support is only complete when the fake adapter contract passes locally and a credentialed smoke is recorded separately. Local tests are not renamed into live proof.

## Cron semantics contract

Cron parity is semantic: schedules, scripts, no-agent jobs, delivery sinks, previews, work directories, model/skill overrides, and history all need independent proof.

## Delegation contract

Subagents and task workers are isolated execution contexts. Their summaries are not proof; parent code must verify files, tests, URLs, or IDs before claiming success.

## Provider routing contract

Provider maturity includes capability matrices, redacted auth status, credential pools, fallback behavior, auxiliary routing, and opt-in credentialed smoke tests.

## CLI and TUI contract

Command parity is more than names. Generated docs, parser shape, slash dispatch, aliases, help text, gateway mapping, and TUI status behavior all need tests.

## Desktop dashboard contract

Desktop and dashboard parity means token-safe auth, local backend readiness, WebSocket tickets, setup/install copy, update state, release smoke, and cross-OS packaging evidence.

## Security approvals contract

Security behavior must be explicit and tested: approval modes, redaction, file safety, dashboard token minimization, policy simulation, and gateway authorization.

## Extension ladder contract

Prefer the existing code path. If that is not enough, add a CLI/docs surface, skill, service-gated tool, plugin, MCP/webhook integration, and only then a new core model-visible tool.
