# Web Dashboard

```bash
aegis ui          # alias: aegis dashboard — opens the browser automatically
```

A zero-build, single-file control panel served from the package (no node, no CDN —
works fully offline) at `http://127.0.0.1:9119`.

## Pages

| Group | Pages |
|---|---|
| — | **Overview** (stat tiles, 30-day spend chart, recent sessions) · **Chat** (markdown, sessions) · **Live activity** (SSE feed) |
| Agent | Sessions · Memory · Skills · Tools |
| Automation | Kanban (with Run board) · Schedules · Webhooks |
| Platform | Models · API keys · MCP servers · Plugins · Pairing · Personas |
| Operations | Analytics (daily spend + by-model) · Curator · Logs · System · Config |

## Niceties

- **Ctrl-K command palette** — jump to any page from the keyboard.
- **5 themes** (Aegis, Midnight, Ember, Hermit, Paper-light) — cycle with the sun icon.
- Toast feedback on every action; secrets are masked and never echoed by the API.

## Security

Binds `127.0.0.1` by default. Set `server.dashboard_token` to require a token
(`?token=…`, `Authorization: Bearer`, or `X-Aegis-Token`). Do not expose the port
publicly without a reverse proxy + auth.
