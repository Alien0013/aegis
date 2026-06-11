# Web Dashboard

```bash
aegis ui          # alias: aegis dashboard — opens the browser automatically
```

A zero-build, single-file control panel served from the package (no node, no CDN —
works fully offline) at `http://127.0.0.1:9119`.

## Pages

| Group | Pages |
|---|---|
| — | **Cockpit** (stat tiles, spend chart, recent sessions) · **Traces** · **Runs** · **Agents** · **Chat** · **Live activity** (SSE feed) |
| Agent | Sessions · Memory · Skills · Tools |
| Automation | Kanban (with Run board) · Schedules · Webhooks |
| Platform | Models · API keys · MCP servers · Plugins · Pairing · Personas |
| Operations | Projects · Worktrees · Evals · Analytics (daily spend + by-model) · Curator · Logs · System · Config |

## Cockpit APIs

The dashboard exposes read-only cockpit APIs used by the no-build frontend:

- `/api/traces` — trace-store rows when available, otherwise session-derived
  traces; accepts `session_id`, `status`, and `source` filters
- `/api/runs` — durable run history from `runs.db`; accepts `surface`, `status`,
  and `session_id` filters for cockpit slicing
- `/api/run?id=...` — one run plus linked session and trace detail when available
- `/api/session?id=...` — transcript plus session detail, including runtime,
  trace id, prompt-part metadata, parent, and child branches for replay/debug
  views. `POST /api/session {"action":"branch","id":"sess_..."}` creates a
  linked child session from the cockpit.
- `/api/agents` — primary agent, typed subagent registry, and background agents
- `/api/projects` and `/api/worktrees` — current project/worktree inventory
- `/api/evals` — eval run store, JSONL eval records, and replay summaries
- `/api/mcp/catalog` — configured MCP servers/catalog metadata; add `?live=1`
  to probe advertised tools, resources, and prompts
- `POST /api/chat` — cockpit chat turn runner. Responses include `run_id`,
  `trace_id`, `turn_id`, `session_id`, the final `reply`, and a compact event
  summary so the Chat page can link directly into the run, trace, and session
  views after each turn.
- `POST /api/chat/stream` — same chat runner over Server-Sent Events. The
  dashboard uses it to show iteration/tool/reasoning progress in the assistant
  bubble while the turn is still running, with `/api/chat` as a compatibility
  fallback.

## Niceties

- **Ctrl-K command palette** — jump to any page from the keyboard.
- The Runs page includes filters for surface, status, and session id. Gateway
  slash commands such as `/status`, `/whoami`, `/model`, `/busy`, and goal
  control actions are recorded as `gateway/control` runs.
- The Traces page includes filters for session id, status, and source so a
  cockpit operator can narrow replay/debug views to one conversation lane.
- The Chat page shows run, trace, session, and turn breadcrumbs for each
  message, plus compact event pills for provider/tool progress. Those links
  open the matching cockpit detail view without leaving the dashboard.
- Chat progress streams live from the dashboard API, so long tool-heavy turns
  update the visible bubble instead of appearing frozen until the final answer.
- **5 themes** (Aegis, Midnight, Ember, Hermit, Paper-light) — cycle with the sun icon.
- Toast feedback on every action; secrets are masked and never echoed by the API.

## Security

Binds `127.0.0.1` by default. Set `server.dashboard_token` to require a token
(`?token=…`, `Authorization: Bearer`, or `X-Aegis-Token`). Do not expose the port
publicly without a reverse proxy + auth.
