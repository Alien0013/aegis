# Tracing and Evals

AEGIS records provider-neutral spans for runs when `tracing.enabled` is true.
The trace store is local SQLite at `~/.aegis/traces.db` by default.

Use `tracing.sample_rate` to reduce trace volume without changing run behavior:

```yaml
tracing:
  enabled: true
  sample_rate: 0.25   # 0 disables trace writes, 1 records every trace
```

Sampling is deterministic per trace id, so fractional rates are stable instead
of flaky across dashboard refreshes and eval replay.

## Trace fields

Each span can include:

`trace_id`, `session_id`, `turn_id`, `span_id`, `parent_span_id`, `kind`,
`status`, `started_at`, `ended_at`, `provider`, `model`, `tool_name`, `cost`,
`cache_read`, `cache_write`, `artifact_ref`.

Initial live spans cover:

- turn spans
- provider calls
- tool calls

Other subsystems can write spans through `aegis.tracing.TraceStore`.

## CLI

```bash
aegis trace list
aegis trace list --session sess_...
aegis trace list --status error
aegis trace show trace_... --json
aegis trace export trace_... --out trace.jsonl
aegis trace export --session sess_... --status ok --out session-ok-traces.jsonl
```

## Provider-free evals

Eval replay uses stored sessions and traces. It does not call a model unless a
caller supplies a custom grader that does so.

Example JSONL suite:

```json
{"name":"final-mentions-alpha","session_id":"sess_...","expected_contains":"alpha"}
{"name":"exact-answer","trace_id":"trace_...","expected_exact":"done"}
{"name":"trace-quality","trace_id":"trace_...","expected_status":"ok","required_tool":"read_file","max_error_spans":0,"max_latency_ms":1500}
```

Run and inspect:

```bash
aegis eval run suite.jsonl
aegis eval list
aegis eval show eval_...
```

Eval run summaries are stored locally under `evals.path` and surfaced in the
dashboard Evals page. Bad JSONL rows or missing replay targets are recorded as
failed `case_error` results and the rest of the suite continues.

## Terminal display

The REPL shows a thinking indicator when provider reasoning is emitted. Control
the display with:

```text
/reasoning off
/reasoning none
/reasoning summary
/reasoning live
```

The status footer includes the latest trace id after a turn so you can jump from
the terminal to `aegis trace show <trace-id>`.
