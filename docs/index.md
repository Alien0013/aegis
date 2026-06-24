# AEGIS Docs

AEGIS is a local-first agent workbench: one auditable Python runtime exposed
through terminal chat, a browser dashboard, an Electron desktop shell,
OpenAI-compatible HTTP, JSON-RPC, Python SDK, ACP, MCP, cron, webhooks, and
messaging gateways.

Use this page as the product map. For implementation status and remaining
Hermes/Codex-style polish, see [feature-parity-matrix.md](feature-parity-matrix.md).

## Start Here

| Need | Doc or command |
| --- | --- |
| Install AEGIS | [install.md](install.md) |
| First terminal session | [quickstart.md](quickstart.md) |
| Full CLI reference | [cli.md](cli.md) |
| Browser dashboard | [dashboard.md](dashboard.md) |
| Desktop app | [../desktop/README.md](../desktop/README.md) |
| OpenAI-compatible API and JSON-RPC | [serve.md](serve.md) |
| Python SDK | [sdk.md](sdk.md) |
| Providers and auth | [providers.md](providers.md) |
| Tools and permissions | [tools.md](tools.md) and [security.md](security.md) |
| Memory, skills, and learning | [memory-skills.md](memory-skills.md) |
| MCP | [mcp.md](mcp.md) |
| Gateway and channels | [gateway.md](gateway.md) |
| Tracing and evals | [tracing-evals.md](tracing-evals.md) |
| Architecture | [architecture.md](architecture.md) |

## Current Product Surfaces

| Surface | Run it | What it shares |
| --- | --- | --- |
| Terminal agent | `aegis` or `aegis chat -q "..."` | Sessions, memory, tools, permissions, traces, run rows, provider routing. |
| Terminal status | `aegis status` | Read-only install/auth/tools/skills/plugins/service status. |
| Browser dashboard | `aegis ui --no-open --port 9119` | Token-gated FastAPI API plus the built React/Vite UI from `aegis/static/web_dist/`. |
| Dashboard development | Start `aegis ui --no-open`, then `cd web && npm run dev` | Vite proxies `/api` to the dashboard backend. |
| Desktop | `aegis desktop` or `cd desktop && npm start` | Electron launches/probes the local dashboard backend and reuses the same AEGIS home. |
| OpenAI-compatible API | `aegis serve --port 8790` | `/v1/chat/completions`, `/v1/models`, sessions, traces, tools, memory, and MCP. |
| JSON-RPC | `aegis rpc` | Local stdio bridge for IDEs and platform adapters. |
| MCP server | `aegis mcp serve` | Exposes AEGIS through MCP using the same policy path. |
| Gateway | `aegis gateway --channels telegram,discord` | Channel sessions, pairing, delivery, shared agent loop, and local state. |
| Automation | `aegis cron`, `aegis kanban`, `aegis watch`, `aegis spec` | Durable scheduled work, task boards, ambient tests, and spec artifacts. |

## Verification From A Clone

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"

python -m aegis.cli.main --help
python -m aegis.cli.main status
python -m aegis.cli.main tools list
bash scripts/run_tests.sh
```

Frontend and desktop checks:

```bash
cd web
npm install
npm run typecheck
npm run build

cd ../desktop
npm install
npm run test:desktop
```

Markdown-only checks are intentionally light in this repo. If a markdown linter
is installed locally, run it over `README.md` and `docs/**/*.md`; otherwise the
practical docs smoke check is reading the rendered files and keeping command
examples aligned with `python -m aegis.cli.main --help`.

## Product Status

AEGIS already has the local runtime, terminal, dashboard, desktop shell, API,
SDK, MCP, gateway, automation, tracing, eval, memory, and skills surfaces in the
repo. The browser dashboard is now session-first, with `/dashboard` as the
overview and `/command-center` as a compact sessions/system/usage overlay.
The remaining parity work is mostly product hardening:

- Dashboard explainability for trace timelines, prompt/context audit, provider
  capability probes, fuller tool provenance, background jobs, cron dry-runs,
  and gateway backpressure metrics.
- Deeper native desktop and full-screen TUI polish: session switching HUDs,
  status bars, model picker overlays, crash history/repair UX, and packaged
  smoke artifact proof.
- Release proof: artifact hashes/SBOM and signed/notarized release evidence
  when credentials are actually configured.
- Generated command and API references so docs cannot drift from the parser.
- API/SDK contract fixtures for streaming, cancellation, auth, run events,
  responses-style behavior, MCP, and eval replay.
- Fake-adapter tests and explicit credentialed live-test instructions for
  messaging channels. These docs do not claim live platform testing is complete.
