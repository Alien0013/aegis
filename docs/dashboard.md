# Web Dashboard

```bash
aegis ui          # alias: aegis dashboard — opens the browser automatically
```

A **React + Vite + TypeScript** control panel (source in `web/`, built to
`aegis/static/web_dist/` and served from the package) at `http://127.0.0.1:9119`.
The built bundle ships with the package, so installs don't need Node — only
developing the frontend does (`cd web && npm run dev`, or `scripts/build_web.sh`
to rebuild). Releases run `scripts/check_web_dist.sh` so the committed bundle
cannot drift from `web/`. It speaks the same token-gated JSON API as every
other surface; chat streams over SSE, and the Cron / Models / API-Keys pages
are full editors.

## Pages

| Group | Pages |
|---|---|
| — | **Overview** (stat tiles, spend, recent sessions) · **Chat** (streaming) |
| Edit | **Config** (edit any setting) · **Models** (pick + set active) · **API Keys** (set secrets) · **Cron** (add/remove/toggle jobs) · **Memory** (add/remove facts) · **Channels** (approve/revoke pairings) |
| View | Sessions · Skills · Tools · Logs · System |

The dashboard is a **control panel, not a viewer** — most pages write back through
the token-gated JSON API (`POST /api/config`, `/api/cron`, `/api/models`,
`/api/keys`, `/api/memory`, `/api/pairing`, …). Secrets are masked and never echoed.

## Desktop app

`desktop/` is an **Electron** wrapper — a native window that launches the AEGIS
dashboard server (random free port + random token via `AEGIS_DASHBOARD_TOKEN`)
and loads it, so you can run the whole harness by double-clicking, no terminal:

```bash
cd desktop && npm install && npm start     # run from source
npm run dist                               # build .dmg / .exe / .AppImage / .deb
```

It only needs `aegis` installed (or `AEGIS_BIN` pointing at the executable). See
`desktop/README.md`.

## Cockpit APIs

The dashboard exposes read-only cockpit APIs used by the no-build frontend:

- `/api/traces` — trace-store rows when available, otherwise session-derived
  traces; accepts `session_id`, `status`, and `source` filters
- `/api/runs` — durable run history from `runs.db`; accepts `surface`, `status`,
  and `session_id` filters for cockpit slicing
- `/api/run?id=...` — one run plus linked session and trace detail when available
- `/api/session?id=...` — transcript plus session detail, including runtime,
  run/trace breadcrumbs, prompt/context metadata, parent, and child branches
  for replay/debug views. `POST /api/session {"action":"branch","id":"sess_..."}`
  creates a linked child session from the cockpit.
- `/api/agents` — primary agent, typed subagent registry, and background agents
- `/api/projects` and `/api/worktrees` — current project/worktree inventory
  with cockpit "chat here" handoff into the Chat page's working directory
- `/api/evals` — eval run store, JSONL eval records, and replay summaries
- `/api/mcp/catalog` — configured MCP servers/catalog metadata; add `?live=1`
  to probe advertised tools, resources, and prompts
- `/api/models` — active provider/model plus the resolver report: transport,
  context window, auth readiness, fallback chain, prompt-routing rules, and
  custom/plugin provider catalog rows
- `POST /api/chat` — cockpit chat turn runner. Responses include `run_id`,
  `trace_id`, `turn_id`, `session_id`, the final `reply`, and a compact event
  summary so the Chat page can link directly into the run, trace, and session
  views after each turn. Send `cwd`, `project`, or `worktree` to run the turn
  in that directory and record project/worktree metadata in run history; send
  `provider`/`provider_name` and `model` to steer only that dashboard turn.
- `POST /api/chat/stream` — same chat runner over Server-Sent Events. The
  dashboard uses it to show iteration/tool/reasoning progress in the assistant
  bubble while the turn is still running, with `/api/chat` as a compatibility
  fallback. Dashboard chat start/progress/final events are also published to
  `/events` so the Live activity page shows cockpit-originated turns alongside
  gateway activity.

## Niceties

- Inventory pages include a client-side filter box for quick scanning.
- Chat progress streams live from the dashboard API, so long tool-heavy turns
  update the visible bubble instead of appearing frozen until the final answer.
- The Chat page can steer a turn's `cwd`, provider, and model while preserving
  the session id across streamed turns.
- **3 themes** (dark, paper, mono) — switch from the sidebar.
- Secrets are masked and never echoed by the API.

## Security

Binds `127.0.0.1` by default. Set `server.dashboard_token` to require a token
(`?token=…`, `Authorization: Bearer`, or `X-Aegis-Token`). Do not expose the port
publicly without a reverse proxy + auth.
