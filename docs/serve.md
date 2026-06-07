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
supported (`"stream": true`).

For research/eval, export run trajectories:

```bash
aegis trajectory export --out runs.jsonl
aegis trajectory stats
```
