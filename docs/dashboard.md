# Web Dashboard

```bash
aegis ui          # alias: aegis dashboard
aegis ui --no-open --port 9119
```

The dashboard is a local React + Vite + TypeScript control panel served by a
FastAPI backend. It opens on `/sessions` so the first screen is resumable work,
not a dense admin wall. `/dashboard` is the calmer overview, `/agents` is the
live monitor for running turns and subagents, and `/command-center` is the
compact sessions/system/usage ops overlay. The built
bundle lives in `aegis/static/web_dist/`, so an installed AEGIS package does not
need Node to open the dashboard. Frontend development happens in `web/`.

The dashboard uses the same token-gated JSON/SSE API as the other surfaces.
Chat turns run through `SurfaceRunner`, so dashboard sessions, tools,
permissions, memory, traces, runs, and provider routing match the terminal,
desktop, SDK, API, gateway, cron, and webhook paths.

## Pages

| Group | Pages |
| --- | --- |
| Workspace | Sessions, Chat, Terminal, Live Agents, Overview |
| Agent | Models, Tools, Skills, Memory, Persona, Schedules, Kanban |
| Integrations | MCP, Channels, Webhooks, Pairing, Accounts, Plugins, Env |
| System | Command Center, Analytics, Files, Logs, Profiles, Docs, System, Config |

The Chat/Terminal surface streams assistant output and tool activity while a
turn is running. Tool rows include arguments, status, duration, result preview,
and error details when available. The final response includes breadcrumbs back
to the run, trace, turn, and session. The Command Center and status APIs also
expose live activity snapshots for running work: phase, active provider, active
tool, subagent counts, iteration, elapsed time, and recent completed activity.

The Live Agents page is the dedicated activity monitor. It shows active runs,
child subagent cards, model/tool call totals, the current tool or provider, and
recent completed activity. In a browser it can pop out to a second window; in
the desktop app it opens as a native Live Agents window.

The Tools page includes current schema health and a permission dry-run panel.
The Channels page includes gateway outbox/dead-letter operations for failed
deliveries. These are local control surfaces; live platform delivery still
requires configured channel credentials and should be smoke-tested separately.

## Setup Selection

The setup wizard has a Tools & skills step. Run
`aegis setup tools --advanced` to choose optional browser, computer, voice, LSP,
and MCP toolsets and optionally restrict model-visible skills. Noninteractive
setup can use `--toolsets` and `--skills` for the same config. It saves
`tools.toolsets` and `skills.allowlist`, then reports model-visible tool counts,
available skills, bundled skills, and plugin tool totals. The dashboard Tools
and Skills pages reflect the same local configuration.

## Development

Run the packaged backend and built bundle:

```bash
aegis ui --no-open --port 9119
```

Develop the Vite app against that backend:

```bash
# Terminal 1, repo root:
aegis ui --no-open --port 9119

# Terminal 2:
cd web
npm install
npm run dev
```

The Vite dev server proxies `/api` and `/dashboard-plugins` to
`VITE_AEGIS_API_TARGET` or `http://127.0.0.1:9119`.

Build and verify the committed bundle:

```bash
cd web
npm run typecheck
npm run build

# Repo root:
scripts/check_web_dist.sh
```

## Desktop Shell

`aegis desktop` launches an Electron shell around the same dashboard runtime.
Electron starts the backend on a random free port with a random token and opens
the chat-first desktop route. The desktop menu can also open `/agents` as a
separate Live Agents window for monitoring long-running work beside chat.

```bash
aegis desktop --doctor
aegis desktop --install-only
aegis desktop

cd desktop
npm install
npm start
npm run test:desktop
```

See [../desktop/README.md](../desktop/README.md) for packaging commands and
release caveats. Signed Windows installers and notarized macOS artifacts are
not claimed unless the required credentials and release artifacts are present.

## Cockpit APIs

Representative dashboard APIs:

- `/api/status` - install, provider, tool, skill, plugin, gateway, and dashboard status.
- `/api/activity` - active and recent live activity snapshots for long-running work.
- `/api/chat` and `/api/chat/stream` - dashboard chat turns with run/session/trace metadata.
- `/api/session?id=...` - transcript and session detail, including breadcrumbs and prompt metadata.
- `/api/runs`, `/api/run?id=...`, `/api/traces`, `/api/evals` - durable run, trace, and eval views.
- `/api/models` - active provider/model plus resolver, auth, capability, and fallback details.
- `/api/tools/validation` - model-visible tool schema validation.
- `/api/tools/permission-dry-run` - structured policy decision without executing the tool.
- `/api/gateway/outbox` and `/api/gateway/dead-letter` - gateway delivery status and recovery actions.
- `/api/mcp/catalog` - configured MCP servers, with optional live probing.

## Security

The dashboard binds to `127.0.0.1` by default. Configure
`server.dashboard_token`, dashboard auth, or a reverse proxy before exposing it
outside localhost. Secrets are masked and are not echoed by the API.
