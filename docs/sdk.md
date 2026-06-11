# Python SDK

AEGIS can be embedded directly from Python. The SDK is a thin facade over the
same runtime used by `aegis chat`, the dashboard, the gateway, and `aegis serve`:
sessions persist to SQLite, traces are written to the configured trace store,
tools and MCP load from the active config, and eval replay uses the same stored
episodes.

```python
from aegis import AegisClient

client = AegisClient()
result = client.run(
    "Summarize the project and list the riskiest open TODOs.",
    title="project audit",
    on_event=lambda event: print(event["type"]),
)

print(result.text)
print(result.session_id, result.trace_id, result.run_id)
```

One `AegisClient` keeps a bounded session-scoped agent cache. Reusing
`session_id` through the same client also reuses the provider object when the
model, provider, working directory, MCP setting, and approval callback are
unchanged. That preserves provider-native continuity such as app-server threads
and prompt/cache warmth.

When a resumed session has explicit runtime controls, the SDK honors them the
same way the CLI and gateway do. `session.meta["runtime_controls"]`, plus
session-level `model` and `provider` overrides, are treated as user steering;
observed runtime telemetry such as `last_runtime` is kept as history and does
not accidentally retarget a reused provider.

Every SDK call writes a durable run row to `runs.db`; `result.run_id` is the
handle for dashboard replay and `/api/run?id=...`.

## Session Continuity

```python
first = client.run("Read the README and remember the important commands.")
second = client.run("Now make a quick checklist.", session_id=first.session_id)

session = client.resume(first.session_id)
branch = client.branch_session(first.session_id, title="alternate plan")
```

`session_id` accepts the same exact id, title, or id-prefix lookup that the CLI
uses. Branching creates a child session with the parent link preserved.
Runtime controls stored on the parent are copied into the branch so alternate
plans keep the same explicit model/provider steering until changed.

## Progress Events

Pass `on_event` to receive the same structured events used by the terminal
renderer:

```python
def show(event):
    if event["type"] == "tool_start":
        print("tool:", event["name"])
    elif event["type"] == "assistant_message":
        print("assistant:", event["text"])

result = client.run("Inspect @diff and explain the change.", on_event=show)
```

Set `stream=True` when the provider should stream deltas. The final
`AegisResult.events` list keeps the full event sequence for custom UIs.
`@file`, `@folder`, `@diff`, `@staged`, `@git:<ref>`, and `@url:` references
expand the same way they do in the CLI; expansion metadata is stored on the
session under `last_context_references`.

## Traces And Evals

```python
trace = client.get_trace(result.trace_id)
recent = client.list_traces(session_id=result.session_id)

replay = client.replay_session(result.session_id)
quality = client.evaluate_trace(result.trace_id)
suite_run = client.run_eval_suite("evals/smoke.jsonl")
```

Turn traces include a prompt snapshot with `system_prompt_hash`,
`system_prompt_tokens`, and named `prompt_parts`. `replay_session(...)` exposes
the same prompt metadata in its replay `meta`, which lets embedded dashboards and
graders explain the exact prompt assembly used for a run.

Eval suites are the same JSONL files accepted by `aegis eval run`, with
`session_id`, `trace_id`, `expected_contains`, `expected_exact`,
`expected_status`, `required_tool`, `max_error_spans`, and `max_latency_ms`
fields.

## Test Providers

Embedding applications can inject a provider factory for deterministic tests:

```python
from aegis.sdk import AegisClient

client = AegisClient(provider_factory=lambda **kwargs: my_provider, include_mcp=False)
result = client.run("hello")
```

When no provider factory is supplied, AEGIS resolves the provider, model,
fallbacks, auth, tools, memory, skills, tracing, and MCP from the active config.
Call `client.close()` to close cached provider transports and MCP clients before
disposing of a long-lived embedding process.
