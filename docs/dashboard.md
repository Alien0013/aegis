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
| Agent | Models, Prompt Audit, Tools, Skills, Memory, Persona, Schedules, Kanban |
| Integrations | MCP, Channels, Webhooks, Pairing, Accounts, Plugins, Env |
| System | Command Center, Analytics, Traces, Files, Logs, Security, Profiles, Docs, System, Config |

The Chat/Terminal surface streams assistant output and tool activity while a
turn is running. Tool rows include arguments, status, duration, result preview,
and error details when available. The final response includes breadcrumbs back
to the run, trace, turn, and session. The Command Center and status APIs also
expose live activity snapshots for running work: phase, active provider, active
tool, subagent counts, iteration, elapsed time, and recent completed activity.

The Live Agents page is the dedicated activity monitor. It shows active runs,
child subagent cards, model/tool call totals, the current tool or provider, and
recent completed activity. In a browser it can pop out to a second window; in
the desktop app it opens as a native Live Agents window. It also includes the
background job panel for retained async delegation tasks, with capacity,
active/completed/failed counts, cancel for running jobs, retry for finished or
failed jobs, and links back to run/trace/session context when available.

The Prompt Audit page shows prompt part hashes, tiering, source labels, cache
stability, token estimates, and context-file warnings without exposing raw
secret-bearing prompt text. The Traces page shows provider calls, tool calls,
status, offsets, duration, previews, and errors from the same trace/session
timeline APIs used by session detail. The Tools page includes current schema
health and a permission dry-run panel.
The Sessions drawer includes a lineage panel for roots, parents, current
session origin, children, descendants, and integrity warnings derived from the
same `parent_id` graph used by branch, compression, subagent, background,
gateway, and cron sessions.
The Skills page includes a Hermes-style quality/provenance report for each
loaded skill: origin, curatable/pinned state, support-file count, duplicate
shadowing, frontmatter validity, prompt-injection warnings, missing env/bin/OS
requirements, and unsafe support-file paths. The API exposes the same report so
curator and install flows can preview why a skill is safe or blocked.
The Channels page includes gateway outbox/dead-letter operations for failed
deliveries. These are local control surfaces; live platform delivery still
requires configured channel credentials and should be smoke-tested separately.
The Plugins page includes an extension contract card for MCP, ACP, and plugin
runtime state: safe mode, manifest and load errors, middleware/hooks, dashboard
API route counts, MCP active/filtered servers, and ACP shared session/trace
status. The MCP page also shows configured include/exclude tool filters per
server so tool selection is visible before a live probe.
The Security page includes the policy simulator for file paths, shell commands,
network URLs, and tool calls. It returns the same allow/prompt/deny explanation
used by the permission engine, file safety checks, SSRF guard, and security
scanner while redacting secret-shaped inputs.

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
separate Live Agents window for monitoring long-running work beside chat. The
desktop connection descriptor exposes a lifecycle snapshot with named states:
`booting`, `probing_backend`, `ready`, `remote_mode`, `updating`, `crashed`,
`repairing`, and `stopped`. The desktop shell operations rail shows that state,
recent transitions, update stage, and bounded crash history beside the chat.

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
- `/api/sessions/{id}/lineage` - session lineage graph with roots, ancestors, children, descendants, safe origin metadata, and warnings.
- `/api/sessions/{id}/prompt-audit` - metadata-only prompt part audit, tiering, hashes, and warnings.
- `/api/sessions/{id}/timeline` and `/api/runs/{id}/timeline` - typed timeline contracts.
- `/api/runs`, `/api/run?id=...`, `/api/traces`, `/api/evals` - durable run, trace, and eval views.
- `/api/models` - active provider/model plus resolver, auth, capability, cached probe, and fallback details.
- `/api/providers/matrix` - provider capability/auth/probe matrix with context/output limits, pricing flags, audio/vision/tool/streaming/reasoning support, redacted last probe, last error, and fallback chain.
- `/api/providers/probe` - bounded live provider probe that stores a redacted last-probe result for the matrix.
- `/api/tools/inventory` - Hermes-style tool inventory with source/provenance, schema hash, availability, required env/auth names, output limits, risk level, and registry rejections.
- `/api/tools/validation` - model-visible tool schema validation.
- `/api/tools/permission-dry-run` - structured policy decision without executing the tool.
- `/api/security/policy-simulate` - file/shell/network/tool policy simulation without execution.
- `/api/skills/manage` - loaded skills plus quality, duplicate, requirement, usage, and provenance metadata.
- `/api/extensions/status` - combined MCP, ACP, and plugin contract state for extension health.
- `/api/background/jobs` - retained background jobs, capacity, completions, and active/failed/completed splits.
- `/api/background/jobs/{id}/cancel` and `/api/background/jobs/{id}/retry` - background recovery controls.
- `/api/gateway/outbox` and `/api/gateway/dead-letter` - gateway delivery status and recovery actions.
- `/api/mcp/catalog` - configured MCP servers, with optional live probing.

## Security

The dashboard binds to `127.0.0.1` by default. Configure
`server.dashboard_token`, dashboard auth, or a reverse proxy before exposing it
outside localhost. Secrets are masked and are not echoed by the API.
