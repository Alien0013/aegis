# AEGIS Dashboard — Internal Map

How the dashboard is wired to the harness core. The dashboard is **not** a reskin: every page
reads/writes the same files and stores the CLI uses, through `/api/*`. The frontend never touches
local files directly.

## Run model
- `aegis ui` / `aegis dashboard` → `aegis/dashboard.py:serve_dashboard` → `dashboard_fastapi.run_dashboard`
  (FastAPI + uvicorn). Also `python -c "from aegis.dashboard_fastapi import run_dashboard; ..."`.
- The server serves the built SPA from `aegis/static/web_dist/` at `/`; the SPA calls `/api/*`.
- `/api/*` is dispatched by `dashboard_fastapi._api_get` (reads) and `_api_post` (writes/actions),
  which delegate to `dashboard.py` `_dashboard_*` helpers and the core modules.
- Chat streams over **SSE** (`POST /api/chat/stream`, `text/event-stream`); the terminal is a real
  **PTY over WebSocket** (`/api/pty`).
- `aegis desktop` → Electron shell in `desktop/` that loads the same dashboard backend.

## Security model (`dashboard_fastapi.py`)
- Binds `127.0.0.1` by default. Loopback peer check (`_peer_allowed`) + token for remote
  (`AEGIS_DASHBOARD_TOKEN`). WebSocket path has its own `_websocket_authorized`.
- Secrets are redacted in every payload; `.env` writes preserve `0600`; config/env writes are
  `atomic_write`. Destructive ops (memory/user reset) are gated by a typed confirmation in the UI.

## Page → endpoint → store → CLI map

| Page | Endpoints (`/api/…`) | Backend | Store / file | CLI equivalent | Security |
|---|---|---|---|---|---|
| Cockpit/Overview | `status`, `cockpit`, `analytics`, `sessions`, `runs`, `agents`, `gateway/status`, `profiles`, `plugins`, `mcp/servers`, `provider-auth` | `dashboard._dashboard_status` + `_cockpit` | session/run/trace stores, config | `aegis status` | read-only |
| Chat | `POST chat/stream` (SSE), `sessions`, `session` | `dashboard_fastapi` chat runner → `SurfaceRunner`/`Agent.run` | session store; honors permission cascade | `aegis` REPL | approvals not bypassed |
| Terminal | `WS /api/pty` | `dashboard_fastapi` PTY | spawns `aegis` TUI behind PTY | `aegis` | loopback + auth; permission cascade |
| Sessions | `sessions`, `session` (GET/POST branch/rename) | `dashboard._dashboard_sessions/_session_detail` | `SessionStore` (state.db) | `aegis sessions` | read; branch/rename writes |
| Runs | `runs`, `run` | `dashboard._dashboard_runs/_run_detail` | run store | `aegis runs` | read-only |
| Traces | `traces`, `trace` | `dashboard._dashboard_traces/_trace_detail` | `TraceStore` (sqlite) | `aegis trace` | read-only |
| Config | `config` (GET/POST), `config/schema`, `config/fields` (PATCH) | `dashboard_fastapi._api_*` + `Config` | `~/.aegis/config.yaml` (atomic) | `aegis config` | validated, atomic; profile-scoped |
| Keys/Secrets | `keys` (GET/POST/DELETE) | `dashboard_fastapi` env handlers | `~/.aegis/.env` (0600, atomic) | `aegis secret set` | redacted; never returns full value |
| Models/Providers | `models`, `providers`, `providers/test` | `dashboard._dashboard_models` + `providers.registry` | config, `auth.json` (status only) | `aegis models` / `aegis doctor` | OAuth shown redacted |
| Memory | `memory` (GET/POST reset via `ops`) | `dashboard._dashboard_memory` + `MemoryManager` | `MEMORY.md`/`USER.md`/history | `aegis memory` | reset gated by confirm |
| Skills | `skills`, `skills/manage` | `dashboard._dashboard_skills` + `SkillsLoader` | skills dirs + bundled | `aegis skills` | toggle writes config |
| Tools | `tools` (GET/POST toggle) | `dashboard._dashboard_tools/_tool_toggle/_toolset_toggle` | `tools.toolsets`/`tools.disabled` | `aegis tools` | toggle writes config |
| MCP | `mcp`, `mcp/servers`, `mcp/catalog` | `dashboard._dashboard_mcp` + `mcp` client | `mcp.servers` config | `aegis mcp` | env redacted |
| Cron | `cron` (GET/POST/PUT/DELETE/trigger) | `dashboard._dashboard_cron` + `CronStore` | cron store | `aegis cron` | writes job store |
| Gateway/Channels | `gateway/status`, `gateway/probe`, `channels`, `pairing` (approve/revoke), `ops` (gateway start/stop/restart) | `dashboard` + `daemon` + `gateway` | gateway config, `.env` tokens, pairing store | `aegis gateway` / `aegis pairing` | systemd-gated control |
| Webhooks | `webhooks` (GET/POST/PUT/DELETE) | `dashboard._dashboard_webhooks` | webhook store | `aegis webhook` | one-time secret on create |
| Profiles | `profiles` (GET/POST set/edit) | `dashboard._profiles_payload` | `~/.aegis/profiles/<name>/` | `aegis profile` | scoped reads/writes |
| Files/Projects/Worktrees | `files`, `files/read`, `files/mkdir`, `projects`, `worktrees` | `dashboard._dashboard_files/_projects/_worktrees` | filesystem (path-guarded) | — | path-traversal guard, read-only default |
| Logs | `logs` | `dashboard_fastapi` log reader | `~/.aegis/logs/aegis.log` | `aegis logs` | tail, redacted |
| Analytics/Cost | `analytics`, `cost` (via usage_log), `insights` | `usage_log` + `insights` | `usage.jsonl`, sessions | `aegis cost` / `aegis insights` | read-only |
| System/Ops | `system`, `ops` (curator/backup/memory-reset/update/services), `ops/checkpoints` | `dashboard._system_info/_ops_status/_ops_action` | home, checkpoints, curator state | `aegis system`/`doctor`/`backup` | destructive ops confirmed |
| Evals/Kanban/Plugins/Review | `evals`/`eval`, `kanban`, `plugins`, `review` | respective `_dashboard_*` | eval/kanban/plugin stores | `aegis evals` etc. | read + gated writes |

## Status vs. the cockpit spec
**Already real (not dummy):** all 26 pages above flow through `/api/*` to real stores; chat
streams (SSE); terminal is a real PTY; config is validated + atomic; secrets are redacted + 0600;
per-tool and per-toolset toggles write config; system operations run curator/backup/reset/update.

**Genuine remaining gaps** (tracked, incremental):
- Config: raw-YAML get/put mode, backup-before-save, reset-section-to-defaults, import/export.
- Memory: `search`, `export`, `import`, learn-candidates queue endpoints.
- Models: auxiliary-model matrix editor (vision/title/summary/compression/search/embeddings/delegation) as a single page; live latency on test.
- Modular backend split (`aegis/dashboard_api/*`) is an architectural preference — the functionality
  already lives in `dashboard.py` + `dashboard_fastapi.py`; split only if it earns its keep.
