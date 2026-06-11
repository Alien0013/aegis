"""Runtime tracing storage and provider-free eval replay foundations."""

from __future__ import annotations


def test_trace_store_writes_lists_gets_spans_and_traces():
    from aegis.tracing import TraceStore

    store = TraceStore()
    store.write_span(
        trace_id="trace_1",
        session_id="sess_1",
        turn_id="turn_1",
        span_id="root",
        kind="model",
        status="ok",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        provider="openai",
        model="gpt-5",
        cost=0.12,
        cache_read=5,
        cache_write=7,
        artifact_ref="artifact://root",
        data={"input_tokens": 100, "output_tokens": 25, "duration_ms": 1000},
    )
    store.write_span(
        trace_id="trace_1",
        session_id="sess_1",
        turn_id="turn_1",
        span_id="tool",
        parent_span_id="root",
        kind="tool",
        status="ok",
        started_at="2026-01-01T00:00:02+00:00",
        ended_at="2026-01-01T00:00:03+00:00",
        tool_name="read_file",
        cost=0.03,
        artifact_ref="artifact://tool",
        data={"args": {"path": "README.md"}, "preview": "contents", "duration_ms": 100},
    )
    store.write_span(
        trace_id="trace_1",
        session_id="sess_1",
        turn_id="turn_1",
        span_id="compact",
        parent_span_id="root",
        kind="compaction",
        status="ok",
        started_at="2026-01-01T00:00:03+00:00",
        ended_at="2026-01-01T00:00:04+00:00",
        data={"messages_before": 12, "messages_after": 5, "duration_ms": 30},
    )

    assert store.get_span("trace_1", "root")["provider"] == "openai"
    spans = store.list_spans(trace_id="trace_1")
    assert [s["span_id"] for s in spans] == ["root", "tool", "compact"]
    assert spans[1]["duration_ms"] == 100

    trace = store.get_trace("trace_1")
    assert trace["span_count"] == 3
    assert trace["status"] == "ok"
    assert round(trace["cost"], 2) == 0.15
    assert trace["cache_read"] == 5 and trace["cache_write"] == 7
    assert trace["artifact_refs"] == ["artifact://root", "artifact://tool"]
    assert trace["provider_calls"] == 1
    assert trace["tool_calls"] == 1
    assert trace["compactions"] == 1
    assert trace["error_spans"] == 0
    assert trace["input_tokens"] == 100
    assert trace["output_tokens"] == 25
    assert trace["duration_ms"] == 4000
    assert trace["providers"] == ["openai"]
    assert trace["models"] == ["gpt-5"]
    assert trace["tools"] == ["read_file"]
    assert trace["kind_counts"]["compaction"] == 1

    listed = store.list_traces(session_id="sess_1")
    assert listed[0]["trace_id"] == "trace_1"
    assert listed[0]["span_count"] == 3
    assert listed[0]["tool_calls"] == 1
    assert listed[0]["compactions"] == 1
    assert listed[0]["models"] == ["gpt-5"]


def test_trace_store_batch_write_defaults_and_rejects_mixed_traces():
    from aegis.tracing import TraceStore

    store = TraceStore()
    trace = store.write_trace(
        [
            {"span_id": "model", "kind": "model"},
            {"span_id": "tool", "parent_span_id": "model", "kind": "tool", "tool_name": "bash"},
        ],
        trace_id="trace_batch",
        session_id="sess_batch",
        turn_id="turn_2",
    )

    assert trace["trace_id"] == "trace_batch"
    assert trace["session_id"] == "sess_batch"
    assert [span["turn_id"] for span in trace["spans"]] == ["turn_2", "turn_2"]

    try:
        store.write_trace([{"trace_id": "a", "span_id": "one"}, {"trace_id": "b", "span_id": "two"}])
        raise AssertionError("mixed trace ids should be rejected")
    except ValueError:
        pass


def test_tracing_sample_rate_controls_trace_creation(tmp_path):
    from types import SimpleNamespace

    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.session import Session
    from aegis.surface import run_control_action
    from aegis.tracing import TraceStore, should_trace

    cfg = Config.load()
    cfg.data.setdefault("tracing", {})["sample_rate"] = 0
    assert should_trace(cfg, "trace_any") is False

    session = Session.create("sampled control")
    agent = SimpleNamespace(config=cfg, session=session, cwd=tmp_path, provider=None)
    result = run_control_action(
        agent,
        lambda _emit: "sampled out",
        config=cfg,
        session=session,
        surface="test",
        kind="control",
        title="sampled control",
        prompt="/sample",
    )

    assert result.run_id.startswith("run_")
    assert result.trace_id == ""
    assert RunStore().get(result.run_id)["trace_id"] == ""
    assert TraceStore.from_config(cfg).list_traces(limit=10) == []

    cfg.data["tracing"]["sample_rate"] = 1
    assert should_trace(cfg, "trace_any") is True
    recorded = run_control_action(
        agent,
        lambda _emit: "sampled in",
        config=cfg,
        session=session,
        surface="test",
        kind="control",
        title="sampled control",
        prompt="/sample",
    )

    assert recorded.trace_id.startswith("trace_")
    assert TraceStore.from_config(cfg).get_trace(recorded.trace_id)["span_count"] == 1


def test_eval_replays_session_and_grades_without_provider_calls():
    from aegis import evals
    from aegis.session import Session, SessionStore
    from aegis.types import Message, ToolCall

    session = Session.create()
    session.messages = [
        Message.system("sys"),
        Message.user("hello"),
        Message.assistant("checking", [ToolCall("call_1", "read_file", {"path": "README.md"})]),
        Message.tool("call_1", "read_file", "contents"),
        Message.assistant("done"),
    ]
    SessionStore().save(session)

    replay = evals.replay_session(session.id)
    assert replay.source == "session"
    assert [step["role"] for step in replay.steps] == ["system", "user", "assistant", "tool", "assistant"]
    assert replay.steps[2]["tool_calls"][0]["name"] == "read_file"

    result = evals.grade_replay(replay)
    assert result["passed"] is True
    assert result["grades"][0]["name"] == "has_steps"

    custom_calls = 0

    def assistant_finished(rep):
        nonlocal custom_calls
        custom_calls += 1
        return {"name": "assistant_finished",
                "passed": rep.steps[-1]["role"] == "assistant",
                "score": 1.0}

    custom = evals.evaluate_session(session.id, graders=[assistant_finished])
    assert custom["passed"] is True and custom_calls == 1
    assert evals.summarize_results([result, custom]) == {"total": 2, "passed": 2, "failed": 0, "score": 1.0}


def test_eval_replays_trace_and_flags_error_spans():
    from aegis import evals
    from aegis.tracing import TraceStore

    store = TraceStore()
    store.write_trace(
        [
            {"span_id": "root", "kind": "model", "status": "ok", "provider": "openai",
             "model": "gpt-5", "data": {"input_tokens": 4, "output_tokens": 2}},
            {"span_id": "tool", "kind": "tool", "status": "error", "tool_name": "bash",
             "parent_span_id": "root", "artifact_ref": "artifact://tool",
             "data": {"args": {"command": "false"}, "preview": "failed", "duration_ms": 12}},
        ],
        trace_id="trace_error",
        session_id="sess_error",
    )

    replay = evals.replay_trace("trace_error", store=store)
    assert replay.source == "trace"
    assert replay.meta["status"] == "error"
    assert [step["span_id"] for step in replay.steps] == ["root", "tool"]
    assert replay.steps[1]["parent_span_id"] == "root"
    assert replay.steps[1]["tool_name"] == "bash"
    assert replay.steps[1]["artifact_ref"] == "artifact://tool"
    assert replay.steps[1]["data"]["args"]["command"] == "false"
    assert replay.steps[1]["duration_ms"] == 12

    result = evals.evaluate_trace("trace_error", store=store)
    assert result["passed"] is False
    assert result["grades"][1]["name"] == "trace_no_error_spans"
    assert result["grades"][1]["details"]["span_ids"] == ["tool"]


def test_eval_store_and_jsonl_suite(tmp_path):
    import json
    from aegis import evals
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    session = Session.create("eval suite")
    session.messages = [Message.user("question"), Message.assistant("the answer is alpha")]
    SessionStore().save(session)

    suite = tmp_path / "suite.jsonl"
    suite.write_text(json.dumps({
        "name": "contains-alpha",
        "session_id": session.id,
        "expected_contains": "alpha",
    }) + "\n", encoding="utf-8")

    store = evals.EvalStore()
    run = evals.run_suite(suite, config=Config.load(), store=store)
    assert run["suite"] == "suite"
    assert run["passed"] == 1 and run["total"] == 1
    listed = store.list_runs()
    assert listed[0]["id"] == run["id"]
    loaded = store.get_run(run["id"])
    assert loaded["summary"]["passed"] == 1


def test_eval_suite_records_case_errors_and_continues(tmp_path):
    import json
    from aegis import evals
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    session = Session.create("eval robust")
    session.messages = [Message.user("question"), Message.assistant("alpha")]
    SessionStore().save(session)

    suite = tmp_path / "robust.jsonl"
    suite.write_text(
        json.dumps({"name": "passes", "session_id": session.id, "expected_contains": "alpha"}) + "\n"
        "{bad json\n"
        + json.dumps({"name": "missing-session", "session_id": "missing", "expected_contains": "x"}) + "\n",
        encoding="utf-8",
    )

    run = evals.run_suite(suite, config=Config.load())

    assert run["total"] == 3
    assert run["passed"] == 1
    assert run["score"] == 0.333
    errors = [r for r in run["results"] if not r["passed"]]
    assert [r["grades"][0]["name"] for r in errors] == ["case_error", "case_error"]
    assert "JSONDecodeError" in errors[0]["error"]
    assert "LookupError" in errors[1]["error"]


def test_trace_and_eval_cli_detail_commands(tmp_path, capsys):
    import json
    from types import SimpleNamespace
    from aegis.cli.main import cmd_eval, cmd_trace
    from aegis.config import Config
    from aegis import evals
    from aegis.tracing import TraceStore

    cfg = Config.load()
    cfg.data.setdefault("tracing", {})["path"] = str(tmp_path / "traces.db")
    cfg.data.setdefault("evals", {})["path"] = str(tmp_path / "evals")

    traces = TraceStore.from_config(cfg)
    traces.write_trace(
        [{"span_id": "root", "kind": "turn", "status": "ok"},
         {"span_id": "model", "kind": "provider_call", "status": "ok", "model": "gpt"}],
        trace_id="trace_cli",
        session_id="sess_cli",
    )
    traces.write_trace(
        [{"span_id": "bad", "kind": "turn", "status": "error"}],
        trace_id="trace_bad",
        session_id="sess_cli",
    )

    assert cmd_trace(SimpleNamespace(action="list", id=None, session=None, limit=10,
                                     spans=False, out=None, json=False), cfg) == 0
    assert "trace_cli" in capsys.readouterr().out

    assert cmd_trace(SimpleNamespace(action="show", id="trace_cli", session=None, limit=10,
                                     spans=False, out=None, json=False), cfg) == 0
    assert "provider_call" in capsys.readouterr().out

    out = tmp_path / "trace.jsonl"
    assert cmd_trace(SimpleNamespace(action="export", id="trace_cli", session=None, limit=10,
                                     spans=False, out=str(out), json=False), cfg) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["trace_id"] == "trace_cli"

    assert cmd_trace(SimpleNamespace(action="list", id=None, session=None, status="error",
                                     limit=10, spans=False, out=None, json=False), cfg) == 0
    listed = capsys.readouterr().out
    assert "trace_bad" in listed and "trace_cli" not in listed

    out_filtered = tmp_path / "trace-filtered.jsonl"
    assert cmd_trace(SimpleNamespace(action="export", id=None, session="sess_cli", status="error",
                                     limit=10, spans=False, out=str(out_filtered), json=False), cfg) == 0
    lines = [json.loads(line) for line in out_filtered.read_text(encoding="utf-8").splitlines()]
    assert [line["trace_id"] for line in lines] == ["trace_bad"]

    store = evals.EvalStore.from_config(cfg)
    run = store.add_run("suite", [{"case": "c1", "passed": True, "score": 1.0, "grades": []}])
    assert cmd_eval(SimpleNamespace(action="show", path=run["id"], limit=10, json=False), cfg) == 0
    assert "suite" in capsys.readouterr().out


def test_trace_eval_suite_quality_gates(tmp_path):
    import json
    from aegis import evals
    from aegis.config import Config
    from aegis.tracing import TraceStore

    cfg = Config.load()
    cfg.data.setdefault("tracing", {})["path"] = str(tmp_path / "traces.db")
    cfg.data.setdefault("evals", {})["path"] = str(tmp_path / "evals")
    TraceStore.from_config(cfg).write_trace(
        [
            {"span_id": "turn", "kind": "turn", "status": "ok",
             "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00"},
            {"span_id": "tool", "kind": "tool", "status": "ok", "tool_name": "read_file",
             "started_at": "2026-01-01T00:00:00.100000+00:00",
             "ended_at": "2026-01-01T00:00:00.200000+00:00"},
        ],
        trace_id="trace_gate",
        session_id="sess_gate",
    )

    suite = tmp_path / "gates.jsonl"
    suite.write_text(json.dumps({
        "name": "trace-quality",
        "trace_id": "trace_gate",
        "expected_status": "ok",
        "required_tool": "read_file",
        "max_error_spans": 0,
        "max_latency_ms": 1500,
    }) + "\n", encoding="utf-8")

    run = evals.run_suite(suite, config=cfg)
    assert run["passed"] == 1
    grades = run["results"][0]["grades"]
    assert [g["name"] for g in grades] == [
        "expected_status", "required_tool", "max_error_spans", "max_latency_ms",
    ]
