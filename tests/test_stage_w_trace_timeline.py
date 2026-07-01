"""Stage W: trace timeline/audit artifact export."""

from __future__ import annotations


def test_trace_store_timeline_exports_ordered_audit_rows(tmp_path):
    from aegis.tracing import TraceStore

    store = TraceStore(tmp_path / "traces.db")
    store.write_trace(
        [
            {
                "span_id": "turn",
                "kind": "turn",
                "status": "ok",
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:00:08+00:00",
                "data": {
                    "prompt": {
                        "system_prompt_hash": "hash_123",
                        "prompt_parts": [{"tier": "stable", "name": "identity"}],
                    },
                    "reason": "text_response",
                    "text": "all done",
                },
            },
            {
                "span_id": "provider",
                "parent_span_id": "turn",
                "kind": "provider_call",
                "status": "ok",
                "provider": "fallback",
                "model": "gpt-5",
                "cost": 0.42,
                "cache_read": 10,
                "cache_write": 2,
                "started_at": "2026-01-01T00:00:02+00:00",
                "ended_at": "2026-01-01T00:00:04+00:00",
                "data": {
                    "api_mode": "responses",
                    "finish_reason": "stop",
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "duration_ms": 2000,
                    "fallback_attempts": [
                        {"index": 0, "event": "pre", "provider": "primary", "model": "claude"},
                        {
                            "index": 0,
                            "event": "error",
                            "status": "error",
                            "provider": "primary",
                            "model": "claude",
                            "duration_ms": 30,
                            "error": {"message": "primary down"},
                        },
                        {"index": 1, "event": "pre", "provider": "fallback", "model": "gpt-5"},
                        {
                            "index": 1,
                            "event": "post",
                            "status": "ok",
                            "provider": "fallback",
                            "model": "gpt-5",
                            "duration_ms": 90,
                        },
                    ],
                },
            },
            {
                "span_id": "tool",
                "parent_span_id": "turn",
                "kind": "tool",
                "status": "ok",
                "tool_name": "bash",
                "artifact_ref": "artifact://tool-output",
                "started_at": "2026-01-01T00:00:05+00:00",
                "ended_at": "2026-01-01T00:00:06+00:00",
                "data": {
                    "args": {"command": "pytest -q"},
                    "preview": "passed",
                    "duration_ms": 1000,
                },
            },
            {
                "span_id": "compact",
                "parent_span_id": "turn",
                "kind": "compaction",
                "status": "ok",
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "started_at": "2026-01-01T00:00:07+00:00",
                "ended_at": "2026-01-01T00:00:08+00:00",
                "data": {"messages_before": 12, "messages_after": 5},
            },
        ],
        trace_id="trace_timeline",
        session_id="sess_timeline",
        turn_id="turn_1",
    )

    rows = store.timeline("trace_timeline")

    assert [row["kind"] for row in rows] == [
        "turn",
        "prompt",
        "provider",
        "provider_attempt",
        "provider_attempt",
        "provider_attempt",
        "provider_attempt",
        "tool",
        "compaction",
        "final",
    ]
    assert [row["started_at"] for row in rows] == sorted(row["started_at"] for row in rows)
    assert rows[1]["prompt"]["system_prompt_hash"] == "hash_123"
    assert rows[2]["provider"] == "fallback"
    assert rows[2]["model"] == "gpt-5"
    assert rows[2]["input_tokens"] == 100
    assert rows[2]["output_tokens"] == 25
    assert rows[2]["cache_read"] == 10
    assert rows[2]["cost"] == 0.42
    assert [(row["attempt_event"], row["provider"], row["status"]) for row in rows[3:7]] == [
        ("pre", "primary", "ok"),
        ("error", "primary", "error"),
        ("pre", "fallback", "ok"),
        ("post", "fallback", "ok"),
    ]
    assert rows[4]["preview"] == "primary down"
    assert rows[7]["tool_name"] == "bash"
    assert rows[7]["artifact_ref"] == "artifact://tool-output"
    assert rows[7]["preview"] == "pytest -q"
    assert rows[8]["label"] == "Compaction 12 to 5"
    assert rows[9]["text"] == "all done"
    assert rows[9]["reason"] == "text_response"
    assert store.get_trace("trace_timeline")["span_count"] == 4


def test_trace_store_timeline_orders_out_of_order_spans_and_reports_turn_errors(tmp_path):
    from aegis.tracing import TraceStore

    store = TraceStore(tmp_path / "traces.db")
    store.write_trace(
        [
            {
                "span_id": "tool",
                "parent_span_id": "turn",
                "kind": "tool",
                "status": "ok",
                "tool_name": "read_file",
                "started_at": "2026-01-01T00:00:02+00:00",
                "ended_at": "2026-01-01T00:00:03+00:00",
            },
            {
                "span_id": "turn",
                "kind": "turn",
                "status": "error",
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": "2026-01-01T00:00:04+00:00",
                "data": {"prompt": "explode", "error": "RuntimeError: boom"},
            },
            {
                "span_id": "provider",
                "parent_span_id": "turn",
                "kind": "model",
                "status": "error",
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "started_at": "2026-01-01T00:00:01+00:00",
                "ended_at": "2026-01-01T00:00:02+00:00",
                "data": {"error": "boom", "duration_ms": 1000},
            },
        ],
        trace_id="trace_error_timeline",
        session_id="sess_error",
    )

    rows = store.timeline("trace_error_timeline")

    assert [row["kind"] for row in rows] == ["turn", "prompt", "model", "tool", "error"]
    assert [row["id"] for row in rows] == ["turn", "turn:prompt", "provider", "tool", "turn:error"]
    assert rows[1]["preview"] == "explode"
    assert rows[2]["status"] == "error"
    assert rows[4]["status"] == "error"
    assert rows[4]["preview"] == "RuntimeError: boom"
    assert rows[4]["started_at"] == "2026-01-01T00:00:04+00:00"
    assert store.timeline("missing_trace") == []
