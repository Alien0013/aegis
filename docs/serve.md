# Serve API

Expose AEGIS as an **OpenAI-compatible** HTTP API — point any OpenAI client at it and
get AEGIS (tools, memory, skills) behind the endpoint.

```bash
aegis serve --port 8790        # POST /v1/chat/completions, GET /v1/models
```

```bash
curl -s http://127.0.0.1:8790/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"hi"}]}'
```

Optional bearer auth via `server.api_key` (or `AEGIS_SERVER_KEY`). Streaming is
supported (`"stream": true`). Requests run through `SurfaceRunner`, so configured
MCP tools, tracing, sessions, skills, and memory are the same as the CLI.

## JSON-RPC Stdio

For local bridges that do not want HTTP, run:

```bash
aegis rpc
```

It speaks newline-delimited JSON-RPC 2.0 over stdin/stdout. Supported methods
include `initialize`, `agent.run`, `sessions.list`, `sessions.get`,
`traces.get`, and `evals.trace`. `agent.run` uses the same `SurfaceRunner` path
as CLI/API/gateway work and emits `agent.event` notifications when event
streaming is enabled.

## Sessions And Metadata

Pass a stable session id with `metadata.session_id`, top-level `session_id`, or the
`X-Aegis-Session` header:

```json
{
  "model": "claude-sonnet-4-6",
  "metadata": {"session_id": "serve:repo-audit"},
  "messages": [{"role": "user", "content": "continue the audit"}]
}
```

Responses include `metadata.session_id`, `metadata.trace_id`, and OpenAI-style
`usage` fields with prompt/completion/cache accounting when the provider reports it.
OpenAI content arrays are accepted for text and image inputs; `system` and
`developer` messages are preserved as request context before the final user turn.

For research/eval, export run trajectories:

```bash
aegis trajectory export --out runs.jsonl
aegis trajectory stats
```
