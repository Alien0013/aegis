"""Generic JSON-RPC agent surface."""

from __future__ import annotations

import io
import json


def _messages(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_rpc_server_initializes_runs_and_exposes_session_trace(monkeypatch, tmp_path):
    from aegis.config import Config
    from aegis.providers import fallback
    from aegis.rpc import RpcServer
    from aegis.types import LLMResponse, Usage
    from conftest import FakeProvider

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = FakeProvider([
        LLMResponse(
            text="rpc ok",
            usage=Usage(input_tokens=11, output_tokens=4, cache_read=2, cache_write=1),
        )
    ])
    monkeypatch.setattr(fallback, "build_with_fallbacks", lambda *_args, **_kwargs: provider)

    out = io.StringIO()
    server = RpcServer(cfg, stdin=io.StringIO(), stdout=out)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    server.handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "agent.run",
        "params": {"prompt": "hello rpc", "title": "rpc smoke", "cwd": str(tmp_path)},
    })

    msgs = _messages(out)
    init = next(m for m in msgs if m.get("id") == 1)
    assert init["result"]["capabilities"]["agent"]["run"] is True
    assert init["result"]["capabilities"]["traces"]["evaluate"] is True
    assert any(m.get("method") == "agent.event" for m in msgs)

    run = next(m for m in msgs if m.get("id") == 2)["result"]
    assert run["text"] == "rpc ok"
    assert run["session_id"].startswith("sess_")
    assert run["trace_id"].startswith("trace_")
    assert run["run_id"].startswith("run_")
    assert run["events"][-1]["type"] == "final"

    server.handle({"jsonrpc": "2.0", "id": 3, "method": "sessions.get",
                   "params": {"id": run["session_id"]}})
    server.handle({"jsonrpc": "2.0", "id": 4, "method": "traces.get",
                   "params": {"id": run["trace_id"]}})
    server.handle({"jsonrpc": "2.0", "id": 5, "method": "evals.trace",
                   "params": {"id": run["trace_id"]}})
    server.handle({"jsonrpc": "2.0", "id": 6, "method": "runs.list",
                   "params": {"session_id": run["session_id"]}})
    server.handle({"jsonrpc": "2.0", "id": 7, "method": "runs.get",
                   "params": {"id": run["run_id"]}})
    server.handle({"jsonrpc": "2.0", "id": 8, "method": "traces.list",
                   "params": {"session_id": run["session_id"]}})
    suite = tmp_path / "rpc_suite.jsonl"
    suite.write_text(
        json.dumps({
            "name": "rpc-final",
            "session_id": run["session_id"],
            "expected_contains": "rpc ok",
        }) + "\n",
        encoding="utf-8",
    )
    server.handle({"jsonrpc": "2.0", "id": 9, "method": "evals.run",
                   "params": {"path": str(suite)}})

    msgs = _messages(out)
    session = next(m for m in msgs if m.get("id") == 3)["result"]["session"]
    assert session["title"] == "rpc smoke"
    assert session["meta"]["surface"] == "rpc"
    assert session["meta"]["runtime"]["provider"] == "fake"
    trace = next(m for m in msgs if m.get("id") == 4)["result"]["trace"]
    assert trace["session_id"] == run["session_id"]
    assert trace["cache_read"] == 2
    assert trace["provider_calls"] == 1
    evaluation = next(m for m in msgs if m.get("id") == 5)["result"]["evaluation"]
    assert evaluation["passed"] is True
    assert next(m for m in msgs if m.get("id") == 6)["result"]["runs"][0]["id"] == run["run_id"]
    assert next(m for m in msgs if m.get("id") == 7)["result"]["run"]["trace_id"] == run["trace_id"]
    assert next(m for m in msgs if m.get("id") == 8)["result"]["traces"][0]["trace_id"] == run["trace_id"]
    eval_run = next(m for m in msgs if m.get("id") == 9)["result"]["eval"]
    assert eval_run["passed"] == 1
    server.handle({"jsonrpc": "2.0", "id": 10, "method": "evals.get",
                   "params": {"id": eval_run["id"]}})
    server.handle({"jsonrpc": "2.0", "id": 11, "method": "evals.list", "params": {}})
    msgs = _messages(out)
    assert next(m for m in msgs if m.get("id") == 10)["result"]["eval"]["results"][0]["case"] == "rpc-final"
    assert next(m for m in msgs if m.get("id") == 11)["result"]["evals"][0]["id"] == eval_run["id"]


def test_rpc_server_reports_protocol_errors():
    from aegis.config import Config
    from aegis.rpc import RpcServer

    out = io.StringIO()
    server = RpcServer(Config.load(), stdin=io.StringIO(), stdout=out)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "agent.run", "params": {}})
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "missing.method", "params": {}})

    msgs = _messages(out)
    assert msgs[0]["error"]["code"] == -32602
    assert "non-empty prompt" in msgs[0]["error"]["message"]
    assert msgs[1]["error"]["code"] == -32601
