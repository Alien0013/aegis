"""OpenAI-compatible HTTP API surface."""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, srv.server_address[1]


def _request(port: int, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps(body or {}).encode()
    req_headers = {"Content-Type": "application/json"} if body is not None else {}
    req_headers.update(headers or {})
    conn.request(method, path, body=payload if body is not None else None, headers=req_headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _raw_request(port: int, method: str, path: str, body: bytes, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


class _Usage:
    def __init__(self, input_tokens=11, output_tokens=7, cache_read=3, cache_write=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read = cache_read
        self.cache_write = cache_write


class _FakeRunner:
    calls = []

    def __init__(self, config, include_mcp=True):
        self.config = config
        self.include_mcp = include_mcp

    def run_prompt(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("on_event"):
            kwargs["on_event"]({"type": "iteration", "n": 1, "max": 2})
            kwargs["on_event"]({"type": "assistant_delta", "text": "hel"})
            kwargs["on_event"]({"type": "tool_start", "name": "read_file", "summary": "reading"})
            kwargs["on_event"]({"type": "assistant_delta", "text": "lo"})
        session_id = kwargs.get("session_id") or "serve:test"
        return SimpleNamespace(
            text="hello",
            session=SimpleNamespace(id=session_id),
            trace_id="trace_http",
            turn_id="turn_http",
            run_id="run_http",
            agent=SimpleNamespace(
                provider=SimpleNamespace(model=kwargs.get("model") or "served-model"),
                budget=SimpleNamespace(usage=_Usage()),
            ),
        )


def test_openai_models_lists_model_ids_not_only_provider_names(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg_paths
    from aegis.config import Config
    from aegis.providers import registry
    from aegis.server import make_handler

    cfg = Config.load()
    cfg.data["model"] = {"provider": "localtest", "default": "local-active-model"}
    cfg.data["custom_providers"] = [{
        "name": "localtest",
        "base_url": "http://local.test/v1",
        "default_model": "local-default-model",
    }]
    plug = cfg_paths.sub("plugins") / "server_provider.py"
    plug.parent.mkdir(parents=True, exist_ok=True)
    plug.write_text(
        "from aegis.providers.registry import ProviderSpec\n"
        "from aegis.providers.base import ApiMode\n"
        "def register(api):\n"
        "    api.register_provider(ProviderSpec(\n"
        "        name='serverplug', api_mode=ApiMode.CHAT_COMPLETIONS,\n"
        "        base_url='http://serverplug.local/v1', default_model='serverplug-model',\n"
        "        context_length=64000, auth_scheme='none'))\n",
        encoding="utf-8",
    )
    srv, port = _serve(make_handler(cfg))
    try:
        status, data = _request(port, "GET", "/v1/models")
    finally:
        srv.shutdown()
        srv.server_close()
        registry.unregister_provider("serverplug")

    assert status == 200
    ids = {row["id"] for row in json.loads(data)["data"]}
    assert "local-active-model" in ids
    assert "local-default-model" in ids
    assert "serverplug-model" in ids
    assert "gpt-5.5" in ids
    assert "localtest" not in ids
    assert "serverplug" not in ids


def test_openai_models_dedupes_ids_but_preserves_provider_owners():
    from aegis.config import Config
    from aegis.server import _models

    cfg = Config.load()
    cfg.data["model"] = {"provider": "openai", "default": "gpt-5.5"}
    cfg.data["custom_providers"] = [
        {
            "name": "mirror",
            "base_url": "http://mirror.local/v1",
            "api_mode": "chat_completions",
            "default_model": "gpt-5.5",
            "context_length": 70_000,
        }
    ]

    rows = _models(cfg)
    matches = [row for row in rows if row["id"] == "gpt-5.5"]

    assert len(matches) == 1
    assert {"openai", "mirror"} <= set(matches[0]["providers"])


def test_openai_server_auth_protects_models_and_rejects_bad_json(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.server import make_handler

    cfg = Config.load()
    cfg.data.setdefault("server", {})["api_key"] = "serve-secret"
    srv, port = _serve(make_handler(cfg))
    try:
        status, _data = _request(port, "GET", "/v1/models")
        authed_status, authed_data = _request(
            port,
            "GET",
            "/v1/models",
            headers={"Authorization": "Bearer serve-secret"},
        )
        bad_status, bad_data = _raw_request(
            port,
            "POST",
            "/v1/chat/completions",
            b"{",
            headers={
                "Authorization": "Bearer serve-secret",
                "Content-Type": "application/json",
            },
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 401
    assert authed_status == 200
    assert json.loads(authed_data)["object"] == "list"
    assert bad_status == 400
    assert json.loads(bad_data)["error"] == "invalid json"


def test_openai_chat_completions_http_nonstream_records_run_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/chat/completions", {
            "model": "served-model",
            "metadata": {
                "session_id": "serve:http",
                "provider": "served-provider",
                "cwd": str(tmp_path / "project"),
            },
            "messages": [
                {"role": "system", "content": "stay terse"},
                {"role": "user", "content": "hi"},
            ],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    body = json.loads(data)
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["metadata"] == {
        "session_id": "serve:http",
        "trace_id": "trace_http",
        "run_id": "run_http",
    }
    assert body["usage"]["prompt_tokens"] == 11
    call = _FakeRunner.calls[0]
    assert call["surface"] == "serve"
    assert call["stream"] is False
    assert call["session_id"] == "serve:http"
    assert call["model"] == "served-model"
    assert call["provider_name"] == "served-provider"
    assert call["cwd"] == str(tmp_path / "project")


def test_openai_chat_completions_aiohttp_transport(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    async def exercise() -> tuple[int, dict]:
        from aiohttp import ClientSession, web

        app = server.make_app(Config.load())
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            assert site._server is not None
            port = site._server.sockets[0].getsockname()[1]
            async with ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "model": "served-model",
                        "metadata": {"session_id": "serve:aiohttp"},
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                ) as resp:
                    return resp.status, await resp.json()
        finally:
            await runner.cleanup()

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    status, body = asyncio.run(exercise())

    assert status == 200
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["metadata"]["session_id"] == "serve:aiohttp"
    assert _FakeRunner.calls[0]["surface"] == "serve"


def test_openai_chat_completions_string_false_is_nonstream(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/chat/completions", {
            "stream": "false",
            "messages": [{"role": "user", "content": "hi"}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    body = json.loads(data)
    assert body["object"] == "chat.completion"
    assert _FakeRunner.calls[0]["stream"] is False


def test_openai_chat_completions_aiohttp_sse_flushes_live(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    class SlowStreamingRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            kwargs["on_event"]({"type": "assistant_delta", "text": "early"})
            time.sleep(1.0)
            session_id = kwargs.get("session_id") or "serve:live"
            return SimpleNamespace(
                text="early late",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_live",
                turn_id="turn_live",
                run_id="run_live",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    async def exercise() -> tuple[int, bytes, float]:
        from aiohttp import ClientSession, web

        app = server.make_app(Config.load())
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            assert site._server is not None
            port = site._server.sockets[0].getsockname()[1]
            started = time.monotonic()
            async with ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
                ) as resp:
                    first = await asyncio.wait_for(resp.content.readline(), timeout=0.8)
                    elapsed = time.monotonic() - started
                    resp.release()
                    return resp.status, first, elapsed
        finally:
            await runner.cleanup()

    monkeypatch.setattr(server, "SurfaceRunner", SlowStreamingRunner)
    status, first, elapsed = asyncio.run(exercise())

    assert status == 200
    assert first.startswith(b"data: ")
    assert b"early" in first
    assert elapsed < 0.8


def test_openai_chat_completions_usage_is_per_response(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class CumulativeRunner:
        def __init__(self, config, include_mcp=True):
            self.total = _Usage(0, 0, 0)
            self.turns = [
                _Usage(5, 2, 1),
                _Usage(7, 3, 0),
            ]

        def run_prompt(self, prompt, **kwargs):
            turn = self.turns.pop(0)
            self.total = _Usage(
                self.total.input_tokens + turn.input_tokens,
                self.total.output_tokens + turn.output_tokens,
                self.total.cache_read + turn.cache_read,
            )
            return SimpleNamespace(
                text="hello",
                usage=turn,
                session=SimpleNamespace(id=kwargs.get("session_id") or "serve:usage"),
                trace_id="trace_usage",
                turn_id="turn_usage",
                run_id="run_usage",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=self.total),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", CumulativeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/chat/completions", {
            "session_id": "serve:usage",
            "messages": [{"role": "user", "content": "first"}],
        })
        second_status, second_data = _request(port, "POST", "/v1/chat/completions", {
            "session_id": "serve:usage",
            "messages": [{"role": "user", "content": "second"}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    first = json.loads(first_data)
    second = json.loads(second_data)
    assert first_status == 200
    assert second_status == 200
    assert first["usage"]["prompt_tokens"] == 5
    assert first["usage"]["completion_tokens"] == 2
    assert second["usage"]["prompt_tokens"] == 7
    assert second["usage"]["completion_tokens"] == 3


def test_openai_chat_completions_stream_sse_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/chat/completions", {
            "model": "served-model",
            "provider": "stream-provider",
            "cwd": str(tmp_path / "stream-project"),
            "stream": True,
            "session_id": "serve:stream",
            "messages": [{"role": "user", "content": "hi"}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    lines = [line.removeprefix("data: ") for line in data.splitlines()
             if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(line) for line in lines[:-1]]
    assert chunks[0]["metadata"]["event"]["type"] == "iteration"
    assert chunks[1]["choices"][0]["delta"]["content"] == "hel"
    assert chunks[2]["metadata"]["event"]["name"] == "read_file"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["prompt_tokens"] == 11
    assert chunks[-1]["usage"]["completion_tokens"] == 7
    assert chunks[-1]["usage"]["prompt_tokens_details"]["cached_tokens"] == 3
    assert chunks[-1]["metadata"]["session_id"] == "serve:stream"
    assert chunks[-1]["metadata"]["trace_id"] == "trace_http"
    assert _FakeRunner.calls[0]["stream"] is True
    assert _FakeRunner.calls[0]["provider_name"] == "stream-provider"
    assert _FakeRunner.calls[0]["cwd"] == str(tmp_path / "stream-project")


def test_server_health_capabilities_and_body_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    monkeypatch.setattr(server, "_MAX_BODY_BYTES", 8)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        health_status, health_data = _request(port, "GET", "/health")
        detailed_status, detailed_data = _request(port, "GET", "/health/detailed")
        caps_status, caps_data = _request(port, "GET", "/v1/capabilities")
        too_large_status, too_large_data = _raw_request(
            port,
            "POST",
            "/v1/chat/completions",
            b'{"messages":[]}',
            headers={"Content-Type": "application/json", "Content-Length": "15"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert health_status == 200
    assert json.loads(health_data)["ok"] is True
    assert detailed_status == 200
    assert json.loads(detailed_data)["max_body_bytes"] == 8
    assert caps_status == 200
    assert json.loads(caps_data)["endpoints"]["responses"] is True
    assert too_large_status == 413
    assert json.loads(too_large_data)["error"] == "request body too large"


def test_server_health_skills_toolsets_and_cors_options(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        health_status, health_data = _request(port, "GET", "/v1/health")
        skills_status, skills_data = _request(port, "GET", "/v1/skills")
        toolsets_status, toolsets_data = _request(port, "GET", "/v1/toolsets")
        options_status, _options_data = _request(port, "OPTIONS", "/v1/chat/completions")
    finally:
        srv.shutdown()
        srv.server_close()

    assert health_status == 200
    assert json.loads(health_data)["ok"] is True
    assert skills_status == 200
    assert json.loads(skills_data)["object"] == "list"
    assert toolsets_status == 200
    assert json.loads(toolsets_data)["object"] == "list"
    assert options_status == 204


def test_responses_create_retrieve_cancel_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "model": "served-model",
            "instructions": "be brief",
            "input": "hello",
            "metadata": {"session_id": "serve:responses"},
        })
        body = json.loads(data)
        response_id = body["id"]
        get_status, get_data = _request(port, "GET", f"/v1/responses/{response_id}")
        cancel_status, cancel_data = _request(port, "POST", f"/v1/responses/{response_id}/cancel", {})
        delete_status, delete_data = _request(port, "DELETE", f"/v1/responses/{response_id}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert body["object"] == "response"
    assert body["output_text"] == "hello"
    assert body["metadata"]["session_id"] == "serve:responses"
    assert get_status == 200
    assert json.loads(get_data)["id"] == response_id
    assert cancel_status == 200
    assert json.loads(cancel_data)["status"] == "cancelled"
    assert delete_status == 200
    assert json.loads(delete_data)["ok"] is True
    assert _FakeRunner.calls[0]["session_id"] == "serve:responses"


def test_responses_persist_store_false_and_previous_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "first",
            "metadata": {"session_id": "serve:persist"},
        })
        response_id = json.loads(data)["id"]
        stateless_status, stateless_data = _request(port, "POST", "/v1/responses", {
            "input": "stateless",
            "store": False,
        })
        stateless_id = json.loads(stateless_data)["id"]
    finally:
        srv.shutdown()
        srv.server_close()

    srv2, port2 = _serve(server.make_handler(Config.load()))
    try:
        get_status, get_data = _request(port2, "GET", f"/v1/responses/{response_id}")
        missing_status, _missing_data = _request(port2, "GET", f"/v1/responses/{stateless_id}")
        chained_status, chained_data = _request(port2, "POST", "/v1/responses", {
            "input": "second",
            "previous_response_id": response_id,
        })
    finally:
        srv2.shutdown()
        srv2.server_close()

    assert status == 200
    assert stateless_status == 200
    assert get_status == 200
    assert json.loads(get_data)["id"] == response_id
    assert missing_status == 404
    assert chained_status == 200
    chained = json.loads(chained_data)
    assert chained["metadata"]["previous_response_id"] == response_id
    assert chained["metadata"]["session_id"] == "serve:persist"


def test_server_session_crud_fork_and_chat(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(port, "POST", "/api/sessions", {"title": "API Session"})
        session_id = json.loads(create_data)["session"]["id"]
        add_status, add_data = _request(
            port,
            "POST",
            f"/api/sessions/{session_id}/messages",
            {"role": "user", "content": "saved"},
        )
        chat_status, chat_data = _request(
            port,
            "POST",
            f"/api/sessions/{session_id}/chat",
            {"prompt": "reply"},
        )
        fork_status, fork_data = _request(
            port,
            "POST",
            f"/api/sessions/{session_id}/fork",
            {"title": "Forked"},
        )
        list_status, list_data = _request(port, "GET", "/api/sessions")
        delete_status, delete_data = _request(port, "DELETE", f"/api/sessions/{session_id}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert create_status == 201
    assert add_status == 200
    assert json.loads(add_data)["message"]["content"] == "saved"
    assert chat_status == 200
    assert json.loads(chat_data)["text"] == "hello"
    assert fork_status == 201
    assert json.loads(fork_data)["session"]["parent_id"] == session_id
    assert list_status == 200
    assert any(row["id"] == session_id for row in json.loads(list_data)["sessions"])
    assert delete_status == 200
    assert json.loads(delete_data)["ok"] is True


def test_server_run_read_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.server import make_handler

    run = RunStore().start(surface="serve", kind="serve", title="read run", prompt="hello")
    RunStore().finish(run["id"], status="ok", result="done")
    srv, port = _serve(make_handler(Config.load()))
    try:
        list_status, list_data = _request(port, "GET", "/v1/runs")
        get_status, get_data = _request(port, "GET", f"/v1/runs/{run['id']}")
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run['id']}/events")
        stream_status, stream_data = _request(
            port,
            "GET",
            f"/v1/runs/{run['id']}/events",
            headers={"Accept": "text/event-stream"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert list_status == 200
    assert any(row["id"] == run["id"] for row in json.loads(list_data)["data"])
    assert get_status == 200
    assert json.loads(get_data)["run"]["id"] == run["id"]
    assert events_status == 200
    assert json.loads(events_data)["ok"] is True
    assert stream_status == 200
    assert "event: done" in stream_data
    assert "data: [DONE]" in stream_data


def test_server_api_jobs_crud_pause_resume_and_run(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    def fake_run_job(config, job, **kwargs):
        job_id = job if isinstance(job, str) else job.id
        return {"ok": True, "job_id": job_id, "reply": "ran"}

    monkeypatch.setattr("aegis.cron.run_job", fake_run_job)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(port, "POST", "/api/jobs", {
            "schedule": "every 1h",
            "prompt": "check status",
            "name": "status check",
            "model": "cron-model",
            "enabled_toolsets": ["core", "web"],
            "workdir": str(tmp_path),
            "no_agent": "false",
        })
        job_id = json.loads(create_data)["id"]
        list_status, list_data = _request(port, "GET", "/api/jobs")
        pause_status, pause_data = _request(port, "POST", f"/api/jobs/{job_id}/pause", {})
        resume_status, resume_data = _request(port, "POST", f"/api/jobs/{job_id}/resume", {})
        patch_status, patch_data = _request(port, "PATCH", f"/api/jobs/{job_id}", {
            "model": "cron-updated",
            "toolsets": "core",
        })
        run_status, run_data = _request(port, "POST", f"/api/jobs/{job_id}/trigger", {})
        delete_status, delete_data = _request(port, "DELETE", f"/api/jobs/{job_id}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert create_status == 201
    created = json.loads(create_data)["job"]
    assert created["model"] == "cron-model"
    assert created["enabled_toolsets"] == ["core", "web"]
    assert created["workdir"] == str(tmp_path)
    assert list_status == 200
    assert any(row["id"] == job_id for row in json.loads(list_data)["data"])
    assert pause_status == 200
    assert json.loads(pause_data)["job"]["enabled"] is False
    assert resume_status == 200
    assert json.loads(resume_data)["job"]["enabled"] is True
    assert patch_status == 200
    assert json.loads(patch_data)["job"]["enabled_toolsets"] == ["core"]
    assert run_status == 200
    assert json.loads(run_data)["job_id"] == job_id
    assert delete_status == 200
    assert json.loads(delete_data)["ok"] is True
