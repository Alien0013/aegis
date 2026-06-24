# Quickstart

This guide gets a local AEGIS checkout from setup to the main product surfaces:
terminal, dashboard, desktop, API, and verification checks.

## Install Or Prepare A Clone

Recommended install:

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
aegis setup
```

Development clone:

```bash
git clone https://github.com/Alien0013/aegis
cd aegis
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"
```

## Configure A Model

API-key path:

```bash
aegis secret set ANTHROPIC_API_KEY
aegis model set anthropic claude-sonnet-4-6
```

Other common choices:

```bash
aegis secret set OPENAI_API_KEY
aegis model set openai gpt-5.5

codex login
aegis model set codex gpt-5.5
aegis model set codex-app-server gpt-5.5

aegis model set ollama llama3.1
```

Check what your environment can actually use:

```bash
aegis status
aegis auth status
aegis model doctor
aegis doctor
```

Use `aegis doctor --probe` only when you intentionally want live provider calls.

## Terminal

```bash
aegis
aegis chat -q "summarize this folder"
aegis chat --continue
aegis chat --image plot.png "what is wrong with this chart?"
aegis batch prompts.txt
aegis status
```

Useful REPL commands:

```text
/help /status /model /provider /tools /skills /memory /usage /compress
/retry /undo /diff /rollback /sessions /resume /branch /new /quit
/plan /proceed /goal /subgoal /background /agents /trace /evals
```

Prompt references work across the REPL, one-shot CLI, SDK/API/gateway, cron,
webhooks, and background jobs:

```text
@file:path[:10-20] @folder:path @diff @staged @git:<ref> @url:https://...
```

## Dashboard

Run the packaged dashboard:

```bash
aegis ui --no-open --port 9119
```

Then open the printed local URL. The dashboard binds to `127.0.0.1` by default
and uses token-gated API access when configured.

Develop the React/Vite UI:

```bash
# Terminal 1, from the repo root:
aegis ui --no-open --port 9119

# Terminal 2:
cd web
npm install
npm run dev
npm run typecheck
npm run build
```

Verify the committed dashboard bundle from the repo root:

```bash
scripts/check_web_dist.sh
```

## Desktop

Run the desktop app from an AEGIS install:

```bash
aegis desktop --doctor
aegis desktop --install-only
aegis desktop
```

Run from source:

```bash
cd desktop
npm install
npm start
npm run test:desktop
```

Build an unpacked app or installer artifacts:

```bash
npm run pack
npm run dist:linux
npm run dist:win
npm run dist:mac
```

Installer builds depend on the host platform and available signing credentials.
AEGIS does not claim signed Windows installers or notarized macOS artifacts are
complete unless those credentials and release artifacts are verifiably present.

## API And RPC

OpenAI-compatible local API:

```bash
aegis serve --port 8790
curl -s http://127.0.0.1:8790/v1/models
curl -s http://127.0.0.1:8790/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"default","messages":[{"role":"user","content":"Say hello from AEGIS"}]}'
```

JSON-RPC stdio:

```bash
aegis rpc
```

MCP:

```bash
aegis mcp list
aegis mcp serve
```

Python SDK:

```python
from aegis import AegisClient

client = AegisClient()
result = client.run("Summarize this repository", title="repo summary")
print(result.text)
print(result.session_id, result.trace_id, result.run_id)
```

## Gateway And Automation

```bash
aegis gateway --channels telegram,discord
aegis pairing list
aegis cron list
aegis kanban list
aegis watch
aegis trace list
aegis eval list
```

Live channel testing requires channel credentials and a real test chat. The
offline docs and CI checks do not prove Telegram, Discord, Slack, Signal,
Matrix, email, or webhook delivery against production services.

## Local Checks

Core:

```bash
python -m aegis.cli.main --help
python -m aegis.cli.main status
python -m aegis.cli.main tools list
bash scripts/run_tests.sh
```

Focused checks:

```bash
python -m pytest -q tests/test_smoke.py
python -m pytest -q tests/test_tool_schema_validation.py
cd web && npm run typecheck && npm run build
cd desktop && npm run test:desktop
```

Use `aegis security audit`, `aegis debug share`, `aegis trace list`, and
`aegis cost --days 30` for operational review before shipping.
