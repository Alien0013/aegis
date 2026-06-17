"""OpenAI-compatible HTTP API surface."""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
import time
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


def _request_with_headers(port: int, method: str, path: str, body: dict | None = None,
                          headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps(body or {}).encode()
    req_headers = {"Content-Type": "application/json"} if body is not None else {}
    req_headers.update(headers or {})
    conn.request(method, path, body=payload if body is not None else None, headers=req_headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    response_headers = dict(resp.getheaders())
    conn.close()
    return resp.status, response_headers, data


def _raw_request(port: int, method: str, path: str, body: bytes, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp.status, data


def _sse_events(data: str):
    event = "message"
    out = []
    for line in data.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            payload = line.removeprefix("data: ").strip()
            if payload == "[DONE]":
                out.append(("done", payload))
            else:
                out.append((event, json.loads(payload)))
            event = "message"
    return out


def _read_sse_event(resp):
    event = "message"
    payload = None
    while True:
        raw = resp.fp.readline()
        if not raw:
            raise AssertionError("stream ended before next SSE event")
        line = raw.decode().strip()
        if not line:
            if payload is not None:
                return event, payload
            continue
        if line.startswith("event: "):
            event = line.removeprefix("event: ").strip()
            continue
        if line.startswith("data: "):
            data = line.removeprefix("data: ").strip()
            payload = data if data == "[DONE]" else json.loads(data)


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


class _BlockingResponsesRunner:
    started = threading.Event()
    release = threading.Event()
    agents = []
    calls = []

    def __init__(self, config, include_mcp=True):
        self.config = config
        self.include_mcp = include_mcp

    @classmethod
    def reset(cls):
        cls.started = threading.Event()
        cls.release = threading.Event()
        cls.agents = []
        cls.calls = []

    def load_or_create_session(self, session_id=None, title=None, history=None, surface="", meta=None):
        return SimpleNamespace(
            id=session_id or "serve:blocking-response",
            title=title or "",
            messages=list(history or []),
            meta=dict(meta or {}),
        )

    def make_agent(self, **kwargs):
        agent = SimpleNamespace(
            cancel_event=threading.Event(),
            provider=SimpleNamespace(model=kwargs.get("model") or "served-model"),
            budget=SimpleNamespace(usage=_Usage()),
        )

        def cancel():
            agent.cancel_event.set()

        agent.cancel = cancel
        self.agents.append(agent)
        return agent

    def run_prompt(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        self.started.set()
        self.release.wait(5)
        session = kwargs.get("session") or SimpleNamespace(id="serve:blocking-response")
        agent = kwargs.get("agent") or SimpleNamespace(
            provider=SimpleNamespace(model="served-model"),
            budget=SimpleNamespace(usage=_Usage()),
        )
        return SimpleNamespace(
            text="late",
            session=session,
            trace_id="trace_response_cancel",
            turn_id="turn_response_cancel",
            run_id="run_response_cancel",
            agent=agent,
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


def test_hermes_session_key_requires_auth_and_rejects_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        unauthenticated_status, unauthenticated_data = _request(
            port,
            "POST",
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}]},
            headers={"X-Hermes-Session-Key": "gateway:user-42"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    cfg = Config.load()
    cfg.data.setdefault("server", {})["api_key"] = "serve-secret"
    srv2, port2 = _serve(server.make_handler(cfg))
    try:
        invalid_status, invalid_data = _request(
            port2,
            "POST",
            "/v1/responses",
            {"input": "hi"},
            headers={
                "Authorization": "Bearer serve-secret",
                "X-Hermes-Session-Key": "x" * 257,
            },
        )
    finally:
        srv2.shutdown()
        srv2.server_close()

    assert unauthenticated_status == 403
    assert "requires API key" in json.loads(unauthenticated_data)["error"]
    assert invalid_status == 400
    assert json.loads(invalid_data)["error"] == "Session key too long"
    assert _FakeRunner.calls == []


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
            "service_tier": "priority",
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
        "service_tier": "priority",
    }
    assert body["usage"]["prompt_tokens"] == 11
    call = _FakeRunner.calls[0]
    assert call["surface"] == "serve"
    assert call["stream"] is False
    assert call["session_id"] == "serve:http"
    assert call["model"] == "served-model"
    assert call["provider_name"] == "served-provider"
    assert call["cwd"] == str(tmp_path / "project")
    assert call["meta"]["runtime_controls"]["service_tier"] == "priority"


def test_hermes_session_key_chat_echoes_and_stays_separate_from_session_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["api_key"] = "serve-secret"
    srv, port = _serve(server.make_handler(cfg))
    try:
        status, headers, data = _request_with_headers(
            port,
            "POST",
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer serve-secret",
                "X-Hermes-Session-Key": "gateway:user-42",
            },
        )
    finally:
        srv.shutdown()
        srv.server_close()

    body = json.loads(data)
    expected_session_id = server._derive_chat_session_id(None, "hi")
    assert status == 200
    assert headers["X-Hermes-Session-Key"] == "gateway:user-42"
    assert headers["X-Hermes-Session-Id"] == expected_session_id
    assert body["metadata"]["session_key"] == "gateway:user-42"
    assert body["metadata"]["session_id"] == expected_session_id
    assert _FakeRunner.calls[0]["session_id"] == expected_session_id
    assert _FakeRunner.calls[0]["meta"]["gateway_session_key"] == "gateway:user-42"


def test_chat_completions_derives_stable_session_for_stateless_frontends(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_headers, first_data = _request_with_headers(
            port,
            "POST",
            "/v1/chat/completions",
            {
                "messages": [
                    {"role": "system", "content": "stay terse"},
                    {"role": "user", "content": "first request"},
                ]
            },
        )
        second_status, second_headers, second_data = _request_with_headers(
            port,
            "POST",
            "/v1/chat/completions",
            {
                "messages": [
                    {"role": "system", "content": "stay terse"},
                    {"role": "user", "content": "first request"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "second request"},
                ]
            },
        )
    finally:
        srv.shutdown()
        srv.server_close()

    expected = server._derive_chat_session_id("stay terse", "first request")
    assert first_status == second_status == 200
    assert first_headers["X-Hermes-Session-Id"] == expected
    assert second_headers["X-Hermes-Session-Id"] == expected
    assert json.loads(first_data)["metadata"]["session_id"] == expected
    assert json.loads(second_data)["metadata"]["session_id"] == expected
    assert [call["session_id"] for call in _FakeRunner.calls] == [expected, expected]


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


def test_openai_chat_completions_rejects_missing_or_empty_user_input(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        missing_status, missing_data = _request(port, "POST", "/v1/chat/completions", {})
        system_status, system_data = _request(port, "POST", "/v1/chat/completions", {
            "messages": [{"role": "system", "content": "be useful"}],
        })
        empty_status, empty_data = _request(port, "POST", "/v1/chat/completions", {
            "messages": [{"role": "user", "content": "   "}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert missing_status == system_status == empty_status == 400
    assert json.loads(missing_data)["error"]["type"] == "invalid_request_error"
    assert "messages" in json.loads(missing_data)["error"]["message"]
    assert json.loads(system_data)["error"]["message"] == "No user message found in messages"
    assert json.loads(empty_data)["error"]["message"] == "No user message found in messages"
    assert _FakeRunner.calls == []


def test_openai_chat_completions_accepts_image_only_user_input(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/chat/completions", {
            "messages": [{
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert json.loads(data)["object"] == "chat.completion"
    assert _FakeRunner.calls[0]["prompt"].images == ["data:image/png;base64,abc"]


def test_openai_chat_completions_rejects_unsupported_content_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        unsupported_status, unsupported_data = _request(port, "POST", "/v1/chat/completions", {
            "messages": [{
                "role": "user",
                "content": [{"type": "file", "file_id": "file_123"}],
            }],
        })
        malformed_status, malformed_data = _request(port, "POST", "/v1/chat/completions", {
            "messages": [{
                "role": "user",
                "content": [{"type": "image_url", "image_url": {}}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert unsupported_status == malformed_status == 400
    unsupported = json.loads(unsupported_data)["error"]
    malformed = json.loads(malformed_data)["error"]
    assert unsupported["code"] == "unsupported_content_part"
    assert unsupported["param"] == "messages[0].content[0].type"
    assert malformed["code"] == "invalid_image_content"
    assert _FakeRunner.calls == []


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
                    lines = []
                    deadline = time.monotonic() + 0.8
                    while time.monotonic() < deadline:
                        line = await asyncio.wait_for(
                            resp.content.readline(),
                            timeout=max(0.05, deadline - time.monotonic()),
                        )
                        lines.append(line)
                        if b"early" in line:
                            break
                    elapsed = time.monotonic() - started
                    resp.release()
                    return resp.status, b"".join(lines), elapsed
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
    events = _sse_events(data)
    assert events[-1] == ("done", "[DONE]")
    progress = [payload for name, payload in events if name == "hermes.tool.progress"]
    assert progress
    assert progress[0]["object"] == "hermes.tool.progress"
    assert progress[0]["type"] == "tool_start"
    assert progress[0]["name"] == "read_file"
    chunks = [
        payload for name, payload in events
        if name == "message" and isinstance(payload, dict)
        and payload.get("object") == "chat.completion.chunk"
    ]
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[1]["metadata"]["event"]["type"] == "iteration"
    assert chunks[2]["choices"][0]["delta"]["content"] == "hel"
    assert chunks[3]["metadata"]["event"]["name"] == "read_file"
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
    caps = json.loads(caps_data)
    assert caps["object"] == "hermes.api_server.capabilities"
    assert caps["legacy_object"] == "capabilities"
    assert caps["transport"] == "aiohttp"
    assert caps["auth"]["type"] == "bearer"
    assert caps["limits"]["max_body_bytes"] == 8
    assert caps["limits"]["responses_auto_truncation_messages"] == 100
    assert caps["endpoints"]["responses"] is True
    assert caps["endpoints"]["jobs"] is True
    routes = {row["name"]: row for row in caps["endpoint_descriptors"]}
    assert routes["responses"]["path"] == "/v1/responses"
    assert routes["responses"]["streaming"] is True
    assert routes["runs.approval"]["methods"] == ["GET", "POST"]
    assert caps["features"]["responses_persistence"] is True
    assert caps["features"]["responses_truncation_auto"] is True
    assert caps["features"]["tool_progress_events"] is True
    assert caps["features"]["session_key_header"] == "X-Hermes-Session-Key"
    assert too_large_status == 413
    assert json.loads(too_large_data)["error"] == "request body too large"


def test_server_health_skills_toolsets_and_cors_options(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    cfg = Config.load()
    cfg.data.setdefault("server", {})["cors_origins"] = ["http://client.local"]
    srv, port = _serve(server.make_handler(cfg))
    try:
        health_status, health_headers, health_data = _request_with_headers(port, "GET", "/v1/health")
        detailed_status, detailed_data = _request(port, "GET", "/v1/health/detailed")
        skills_status, skills_data = _request(port, "GET", "/v1/skills")
        toolsets_status, toolsets_data = _request(port, "GET", "/v1/toolsets")
        options_status, options_headers, _options_data = _request_with_headers(
            port,
            "OPTIONS",
            "/v1/chat/completions",
            headers={"Origin": "http://client.local"},
        )
        blocked_status, _blocked_headers, blocked_data = _request_with_headers(
            port,
            "GET",
            "/v1/health",
            headers={"Origin": "http://evil.local"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert health_status == 200
    assert json.loads(health_data)["ok"] is True
    assert detailed_status == 200
    detailed = json.loads(detailed_data)
    assert detailed["ok"] is True
    assert detailed["runtime"]["active_runs"] == 0
    assert detailed["stores"]["responses"]["responses"] == 0
    assert detailed["stores"]["runs"]["count"] == 0
    assert detailed["stores"]["jobs"]["count"] == 0
    assert detailed["diagnostics"]["cross_session"]["ok"] is True
    assert "session_run_links" in {row["id"] for row in detailed["diagnostics"]["cross_session"]["checks"]}
    assert "resume_pending" in {row["id"] for row in detailed["diagnostics"]["cross_session"]["checks"]}
    assert health_headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert health_headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in health_headers
    assert skills_status == 200
    assert json.loads(skills_data)["object"] == "list"
    assert toolsets_status == 200
    assert json.loads(toolsets_data)["object"] == "list"
    assert options_status == 204
    assert options_headers["Access-Control-Allow-Origin"] == "http://client.local"
    assert options_headers["Access-Control-Max-Age"] == "600"
    assert blocked_status == 403
    assert json.loads(blocked_data)["error"] == "cors origin not allowed"


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
    assert body["error"] is None
    assert body["incomplete_details"] is None
    assert body["parallel_tool_calls"] is True
    assert body["instructions"] == "be brief"
    assert body["previous_response_id"] is None
    assert body["metadata"]["session_id"] == "serve:responses"
    assert get_status == 200
    assert json.loads(get_data)["id"] == response_id
    assert cancel_status == 200
    assert json.loads(cancel_data)["status"] == "cancelled"
    assert delete_status == 200
    assert json.loads(delete_data)["ok"] is True
    assert _FakeRunner.calls[0]["session_id"] == "serve:responses"


def test_responses_rejects_missing_or_empty_user_input(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        missing_status, missing_data = _request(port, "POST", "/v1/responses", {})
        empty_status, empty_data = _request(port, "POST", "/v1/responses", {"input": ""})
        assistant_status, assistant_data = _request(port, "POST", "/v1/responses", {
            "input": [{"role": "assistant", "content": "not a user prompt"}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert missing_status == empty_status == assistant_status == 400
    assert json.loads(missing_data)["error"] == {
        "message": "Missing 'input' field",
        "type": "invalid_request_error",
        "param": "input",
    }
    assert json.loads(empty_data)["error"]["message"] == "No user message found in input"
    assert json.loads(assistant_data)["error"]["message"] == "No user message found in input"
    assert _FakeRunner.calls == []


def test_responses_accepts_messages_alias_and_image_only_input(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "messages": [{
                "role": "user",
                "content": [{"type": "input_image", "image_url": "data:image/png;base64,abc"}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert json.loads(data)["object"] == "response"
    assert _FakeRunner.calls[0]["prompt"].images == ["data:image/png;base64,abc"]


def test_responses_rejects_unsupported_content_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [{
                "role": "user",
                "content": [{"type": "document", "document": {"id": "doc_123"}}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "unsupported_content_part"
    assert error["param"] == "input[0].content[0].type"
    assert _FakeRunner.calls == []


def test_responses_echo_hermes_session_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["api_key"] = "serve-secret"
    srv, port = _serve(server.make_handler(cfg))
    try:
        status, headers, data = _request_with_headers(
            port,
            "POST",
            "/v1/responses",
            {"model": "served-model", "input": "hello"},
            headers={
                "Authorization": "Bearer serve-secret",
                "X-Hermes-Session-Key": "gateway:user-42",
            },
        )
    finally:
        srv.shutdown()
        srv.server_close()

    body = json.loads(data)
    assert status == 200
    assert headers["X-Hermes-Session-Key"] == "gateway:user-42"
    assert headers["X-Hermes-Session-Id"] == "serve:test"
    assert body["metadata"]["session_key"] == "gateway:user-42"
    assert _FakeRunner.calls[0]["session_id"] is None
    assert _FakeRunner.calls[0]["meta"]["gateway_session_key"] == "gateway:user-42"


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
            "instructions": "keep it concise",
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
    second_call = _FakeRunner.calls[-1]
    assert [m.content for m in second_call["history"][:3]] == [
        "<system_instructions>\nkeep it concise\n</system_instructions>",
        "first",
        "hello",
    ]
    assert second_call["prompt"].content == "second"


def test_responses_missing_previous_id_404s_without_running(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "second",
            "previous_response_id": "resp_missing",
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 404
    assert "Previous response not found" in json.loads(data)["error"]
    assert _FakeRunner.calls == []


def test_responses_conversation_maps_to_latest_response(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "first",
            "conversation": "thread-a",
        })
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "input": "second",
            "conversation": {"id": "thread-a"},
        })
        conflict_status, conflict_data = _request(port, "POST", "/v1/responses", {
            "input": "bad",
            "conversation": "thread-a",
            "previous_response_id": json.loads(first_data)["id"],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == 200
    assert second_status == 200
    second = json.loads(second_data)
    assert second["conversation"] == "thread-a"
    assert second["previous_response_id"] == json.loads(first_data)["id"]
    assert [m.content for m in _FakeRunner.calls[1]["history"][:2]] == ["first", "hello"]
    assert conflict_status == 400
    assert "Cannot use both" in json.loads(conflict_data)["error"]


def test_responses_truncation_auto_limits_previous_history(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    long_input = [{"role": "user", "content": f"msg {i}"} for i in range(150)]
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": long_input,
        })
        response_id = json.loads(first_data)["id"]
        auto_status, _auto_data = _request(port, "POST", "/v1/responses", {
            "input": "follow up",
            "previous_response_id": response_id,
            "truncation": "auto",
        })
        full_status, _full_data = _request(port, "POST", "/v1/responses", {
            "input": "follow up without truncation",
            "previous_response_id": response_id,
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == 200
    assert auto_status == 200
    assert full_status == 200
    auto_history = _FakeRunner.calls[1]["history"]
    full_history = _FakeRunner.calls[2]["history"]
    assert len(auto_history) == 100
    assert len(full_history) == 151
    assert auto_history[0].content == "msg 51"
    assert auto_history[-1].content == "hello"


def test_responses_stream_sse_has_openai_event_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "stream": True,
            "input": "hello",
            "metadata": {"session_id": "serve:responses-stream"},
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    events = _sse_events(data)
    names = [name for name, _payload in events]
    assert names[:4] == [
        "response.created",
        "aegis.event",
        "response.output_item.added",
        "response.output_text.delta",
    ]
    assert "response.output_text.done" in names
    assert "response.output_item.done" in names
    assert names[-2:] == ["response.completed", "done"]
    payloads = [payload for _name, payload in events if isinstance(payload, dict)]
    assert [p["sequence_number"] for p in payloads] == list(range(len(payloads)))
    delta = next(payload for name, payload in events if name == "response.output_text.delta")
    done = next(payload for name, payload in events if name == "response.output_text.done")
    assert delta["output_index"] == 0
    assert delta["content_index"] == 0
    assert delta["item_id"].startswith("msg_")
    assert done["text"] == "hello"
    assert done["item_id"] == delta["item_id"]
    assert _FakeRunner.calls[0]["stream"] is True


def test_responses_stream_cancel_signals_live_agent_and_preserves_cancelled(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingResponsesRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingResponsesRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(
            "POST",
            "/v1/responses",
            body=json.dumps({
                "stream": True,
                "input": "wait for cancel",
                "metadata": {"session_id": "serve:response-cancel"},
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        event, payload = _read_sse_event(resp)
        assert event == "response.created"
        response_id = payload["response"]["id"]
        assert _BlockingResponsesRunner.started.wait(1)

        cancel_status, cancel_data = _request(port, "POST", f"/v1/responses/{response_id}/cancel", {})
        _BlockingResponsesRunner.release.set()
        tail = resp.read().decode()
        get_status, get_data = _request(port, "GET", f"/v1/responses/{response_id}")
    finally:
        _BlockingResponsesRunner.release.set()
        conn.close()
        srv.shutdown()
        srv.server_close()

    assert cancel_status == 200
    assert json.loads(cancel_data)["status"] == "cancelled"
    assert _BlockingResponsesRunner.agents
    assert _BlockingResponsesRunner.agents[0].cancel_event.is_set()
    events = _sse_events(tail)
    names = [name for name, _payload in events]
    assert "response.cancelled" in names
    assert "response.completed" not in names
    assert get_status == 200
    stored = json.loads(get_data)
    assert stored["status"] == "cancelled"
    assert stored.get("output_text", "") != "late"


def test_responses_aiohttp_stream_disconnect_cancels_live_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingResponsesRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingResponsesRunner)

    async def exercise() -> bool:
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
                    f"http://127.0.0.1:{port}/v1/responses",
                    json={
                        "stream": True,
                        "input": "disconnect me",
                        "metadata": {"session_id": "serve:response-disconnect"},
                    },
                ) as resp:
                    assert resp.status == 200
                    event_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert event_line == b"event: response.created\n"
                    assert data_line.startswith(b"data: ")
                    assert _BlockingResponsesRunner.started.wait(1)
                    resp.close()
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if (
                            _BlockingResponsesRunner.agents
                            and _BlockingResponsesRunner.agents[0].cancel_event.is_set()
                        ):
                            return True
                        await asyncio.sleep(0.05)
                    return False
        finally:
            _BlockingResponsesRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True


def test_responses_stream_failure_persists_failed_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class FailingRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            kwargs["on_event"]({"type": "assistant_delta", "text": "partial"})
            raise RuntimeError("boom")

    monkeypatch.setattr(server, "SurfaceRunner", FailingRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "stream": True,
            "input": "hello",
            "metadata": {"session_id": "serve:responses-failed"},
        })
        events = _sse_events(data)
        failed = next(payload for name, payload in events if name == "response.failed")
        response_id = failed["response"]["id"]
        get_status, get_data = _request(port, "GET", f"/v1/responses/{response_id}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert failed["response"]["status"] == "failed"
    assert failed["response"]["output_text"] == "partial"
    assert failed["error"]["message"] == "RuntimeError: boom"
    assert events[-1] == ("done", "[DONE]")
    assert get_status == 200
    stored = json.loads(get_data)
    assert stored["status"] == "failed"
    assert stored["output_text"] == "partial"
    assert stored["error"]["message"] == "RuntimeError: boom"


def test_responses_stream_maps_tools_to_function_call_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class ToolStreamRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            kwargs["on_event"]({
                "type": "tool_start",
                "id": "call_search",
                "name": "search",
                "args": {"query": "aegis"},
            })
            kwargs["on_event"]({
                "type": "tool_result",
                "id": "call_search",
                "name": "search",
                "preview": "found docs",
            })
            kwargs["on_event"]({"type": "assistant_delta", "text": "done"})
            session_id = kwargs.get("session_id") or "serve:tool-stream"
            return SimpleNamespace(
                text="done",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_tool_stream",
                turn_id="turn_tool_stream",
                run_id="run_tool_stream",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", ToolStreamRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "stream": True,
            "input": "search",
            "metadata": {"session_id": "serve:tool-stream"},
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    events = _sse_events(data)
    added = [payload["item"] for name, payload in events
             if name == "response.output_item.added"]
    done = [payload["item"] for name, payload in events
            if name == "response.output_item.done"]
    function_call = next(item for item in added if item["type"] == "function_call")
    function_output = next(item for item in added if item["type"] == "function_call_output")
    completed = next(payload for name, payload in events if name == "response.completed")

    assert function_call["name"] == "search"
    assert function_call["call_id"] == "call_search"
    assert json.loads(function_call["arguments"]) == {"query": "aegis"}
    assert function_output["call_id"] == "call_search"
    assert function_output["output"][0]["text"] == "found docs"
    assert any(item["type"] == "function_call" and item["status"] == "completed" for item in done)
    assert any(item["type"] == "function_call_output" for item in done)
    assert [item["type"] for item in completed["response"]["output"]] == [
        "function_call",
        "function_call_output",
        "message",
    ]


def test_responses_nonstream_maps_tools_to_output_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class ToolBatchRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            session_id = kwargs.get("session_id") or "serve:tool-batch"
            return SimpleNamespace(
                text="done",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_tool_batch",
                turn_id="turn_tool_batch",
                run_id="run_tool_batch",
                events=[
                    {
                        "type": "tool_start",
                        "id": "call_search",
                        "name": "search",
                        "args": {"query": "aegis"},
                    },
                    {
                        "type": "tool_result",
                        "id": "call_search",
                        "name": "search",
                        "preview": "found docs",
                    },
                ],
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", ToolBatchRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "search",
            "metadata": {"session_id": "serve:tool-batch"},
        })
        response = json.loads(data)
        get_status, get_data = _request(port, "GET", f"/v1/responses/{response['id']}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert [item["type"] for item in response["output"]] == [
        "function_call",
        "function_call_output",
        "message",
    ]
    assert response["output"][0]["name"] == "search"
    assert json.loads(response["output"][0]["arguments"]) == {"query": "aegis"}
    assert response["output"][1]["call_id"] == "call_search"
    assert response["output"][1]["output"][0]["text"] == "found docs"
    assert response["output_text"] == "done"
    assert get_status == 200
    stored = json.loads(get_data)
    assert [item["type"] for item in stored["output"]] == [
        "function_call",
        "function_call_output",
        "message",
    ]


def test_responses_idempotency_key_replays_matching_request(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class CountingRunner:
        calls = 0

        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            type(self).calls += 1
            session_id = kwargs.get("session_id") or "serve:idem"
            return SimpleNamespace(
                text=f"reply-{type(self).calls}",
                session=SimpleNamespace(id=session_id),
                trace_id=f"trace_{type(self).calls}",
                turn_id=f"turn_{type(self).calls}",
                run_id=f"run_{type(self).calls}",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", CountingRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        headers = {"Idempotency-Key": "idem-resp"}
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "same",
            "metadata": {"session_id": "serve:idem"},
        }, headers=headers)
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "input": "same",
            "metadata": {"session_id": "serve:idem"},
        }, headers=headers)
        third_status, third_data = _request(port, "POST", "/v1/responses", {
            "input": "different",
            "metadata": {"session_id": "serve:idem"},
        }, headers=headers)
    finally:
        srv.shutdown()
        srv.server_close()

    first = json.loads(first_data)
    second = json.loads(second_data)
    third = json.loads(third_data)
    assert first_status == second_status == third_status == 200
    assert second["id"] == first["id"]
    assert second["output_text"] == "reply-1"
    assert third["id"] != first["id"]
    assert third["output_text"] == "reply-2"
    assert CountingRunner.calls == 2


def test_chat_completions_idempotency_key_replays_matching_request(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class CountingRunner:
        calls = 0

        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            type(self).calls += 1
            session_id = kwargs.get("session_id") or "serve:chat-idem"
            return SimpleNamespace(
                text=f"chat-{type(self).calls}",
                session=SimpleNamespace(id=session_id),
                trace_id=f"trace_chat_{type(self).calls}",
                turn_id=f"turn_chat_{type(self).calls}",
                run_id=f"run_chat_{type(self).calls}",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", CountingRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        headers = {"Idempotency-Key": "idem-chat"}
        body = {"messages": [{"role": "user", "content": "same"}]}
        first_status, first_data = _request(port, "POST", "/v1/chat/completions", body, headers=headers)
        second_status, second_data = _request(port, "POST", "/v1/chat/completions", body, headers=headers)
        third_status, third_data = _request(
            port,
            "POST",
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "different"}]},
            headers=headers,
        )
    finally:
        srv.shutdown()
        srv.server_close()

    first = json.loads(first_data)
    second = json.loads(second_data)
    third = json.loads(third_data)
    assert first_status == second_status == third_status == 200
    assert second["id"] == first["id"]
    assert second["choices"][0]["message"]["content"] == "chat-1"
    assert third["id"] != first["id"]
    assert third["choices"][0]["message"]["content"] == "chat-2"
    assert CountingRunner.calls == 2


def test_chat_completions_idempotency_key_single_flights_concurrent_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class SlowRunner:
        calls = 0
        entered = threading.Event()
        release = threading.Event()

        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            type(self).calls += 1
            type(self).entered.set()
            assert type(self).release.wait(5)
            session_id = kwargs.get("session_id") or "serve:chat-singleflight"
            return SimpleNamespace(
                text="chat-singleflight",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_chat_singleflight",
                turn_id="turn_chat_singleflight",
                run_id="run_chat_singleflight",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", SlowRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    results: list[tuple[int, str]] = []
    try:
        headers = {"Idempotency-Key": "idem-chat-flight"}
        body = {"messages": [{"role": "user", "content": "same"}]}
        threads = [
            threading.Thread(target=lambda: results.append(
                _request(port, "POST", "/v1/chat/completions", body, headers=headers)
            ))
            for _ in range(2)
        ]
        threads[0].start()
        assert SlowRunner.entered.wait(5)
        threads[1].start()
        time.sleep(0.2)
        assert SlowRunner.calls == 1
    finally:
        SlowRunner.release.set()
        for thread in locals().get("threads", []):
            thread.join(timeout=5)
        srv.shutdown()
        srv.server_close()

    assert len(results) == 2
    assert {status for status, _data in results} == {200}
    bodies = [json.loads(data) for _status, data in results]
    assert bodies[0]["id"] == bodies[1]["id"]
    assert [body["choices"][0]["message"]["content"] for body in bodies] == [
        "chat-singleflight",
        "chat-singleflight",
    ]
    assert SlowRunner.calls == 1


def test_responses_idempotency_key_single_flights_concurrent_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class SlowRunner:
        calls = 0
        entered = threading.Event()
        release = threading.Event()

        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            type(self).calls += 1
            type(self).entered.set()
            assert type(self).release.wait(5)
            session_id = kwargs.get("session_id") or "serve:response-singleflight"
            return SimpleNamespace(
                text="response-singleflight",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_response_singleflight",
                turn_id="turn_response_singleflight",
                run_id="run_response_singleflight",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", SlowRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    results: list[tuple[int, str]] = []
    try:
        headers = {"Idempotency-Key": "idem-response-flight"}
        body = {"input": "same", "metadata": {"session_id": "serve:response-singleflight"}}
        threads = [
            threading.Thread(target=lambda: results.append(
                _request(port, "POST", "/v1/responses", body, headers=headers)
            ))
            for _ in range(2)
        ]
        threads[0].start()
        assert SlowRunner.entered.wait(5)
        threads[1].start()
        time.sleep(0.2)
        assert SlowRunner.calls == 1
    finally:
        SlowRunner.release.set()
        for thread in locals().get("threads", []):
            thread.join(timeout=5)
        srv.shutdown()
        srv.server_close()

    assert len(results) == 2
    assert {status for status, _data in results} == {200}
    bodies = [json.loads(data) for _status, data in results]
    assert bodies[0]["id"] == bodies[1]["id"]
    assert [body["output_text"] for body in bodies] == ["response-singleflight", "response-singleflight"]
    assert SlowRunner.calls == 1


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
    listed = json.loads(list_data)
    assert listed["object"] == "list"
    assert listed["data"] == listed["sessions"]
    assert listed["offset"] == 0
    assert listed["has_more"] is False
    assert any(row["id"] == session_id for row in listed["data"])
    assert delete_status == 200
    assert json.loads(delete_data)["ok"] is True


def test_server_session_create_and_fork_honor_hermes_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(
            port,
            "POST",
            "/api/sessions",
            {
                "id": "api-explicit",
                "title": "Explicit Session",
                "model": "gpt-test",
                "system_prompt": "You are testing.",
                "metadata": {"client": "hermes-compatible"},
            },
        )
        duplicate_status, duplicate_data = _request(
            port,
            "POST",
            "/api/sessions",
            {"session_id": "api-explicit", "title": "Duplicate"},
        )
        add_status, _add_data = _request(
            port,
            "POST",
            "/api/sessions/api-explicit/messages",
            {"role": "user", "content": "saved"},
        )
        fork_status, fork_data = _request(
            port,
            "POST",
            "/api/sessions/api-explicit/fork",
            {"id": "api-child", "title": "Explicit Fork"},
        )
        messages_status, messages_data = _request(port, "GET", "/api/sessions/api-child/messages?limit=2&offset=0")
    finally:
        srv.shutdown()
        srv.server_close()

    assert create_status == 201
    created = json.loads(create_data)["session"]
    assert created["id"] == "api-explicit"
    assert created["title"] == "Explicit Session"
    assert created["meta"]["model"] == "gpt-test"
    assert created["meta"]["runtime_controls"]["model"] == "gpt-test"
    assert created["meta"]["system_prompt"] == "You are testing."
    assert created["meta"]["client"] == "hermes-compatible"
    assert created["messages"][0]["role"] == "system"
    assert duplicate_status == 409
    assert json.loads(duplicate_data)["code"] == "session_exists"
    assert add_status == 200
    assert fork_status == 201
    forked = json.loads(fork_data)["session"]
    assert forked["id"] == "api-child"
    assert forked["parent_id"] == "api-explicit"
    assert forked["title"] == "Explicit Fork"
    assert messages_status == 200
    message_payload = json.loads(messages_data)
    assert message_payload["object"] == "list"
    assert message_payload["session_id"] == "api-child"
    assert message_payload["limit"] == 2
    assert message_payload["offset"] == 0
    assert message_payload["messages"] == message_payload["data"]
    messages = message_payload["data"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == "saved"


def test_server_session_chat_stream_uses_sse_cors_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["cors_origins"] = ["http://client.local"]
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/api/sessions", {"title": "Stream Session"})
        session_id = json.loads(create_data)["session"]["id"]
        stream_status, headers, stream_data = _request_with_headers(
            port,
            "POST",
            f"/api/sessions/{session_id}/chat/stream",
            {"prompt": "reply"},
            headers={"Origin": "http://client.local"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert create_status == 201
    assert stream_status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    assert headers["Access-Control-Allow-Origin"] == "http://client.local"
    assert headers["X-Accel-Buffering"] == "no"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Hermes-Session-Id"] == session_id
    events = _sse_events(stream_data)
    names = [name for name, _payload in events]
    assert names[:2] == ["run.started", "message.started"]
    assert "assistant.delta" in names
    assert names[-3:] == ["run.completed", "done", "done"]
    assert events[-1] == ("done", "[DONE]")
    started = events[0][1]
    completed = next(payload for name, payload in events if name == "assistant.completed")
    assert started["session_id"] == session_id
    assert started["user_message"]["content"] == "reply"
    assert completed["content"] == "hello"
    assert completed["trace_id"] == "trace_http"


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
    assert any(
        row["id"] == run["id"]
        and row["run_id"] == run["id"]
        and row["object"] == "hermes.run"
        and row["output"] == "done"
        for row in json.loads(list_data)["data"]
    )
    assert get_status == 200
    get_body = json.loads(get_data)
    assert get_body["object"] == "hermes.run"
    assert get_body["run_id"] == run["id"]
    assert get_body["status"] == "completed"
    assert get_body["output"] == "done"
    assert get_body["run"]["id"] == run["id"]
    assert events_status == 200
    assert json.loads(events_data)["ok"] is True
    assert stream_status == 200
    assert "event: done" in stream_data
    assert "data: [DONE]" in stream_data


class _BlockingRunRunner:
    started = threading.Event()
    release = threading.Event()
    agents = []
    calls = []

    def __init__(self, config, include_mcp=True):
        self.config = config
        self.include_mcp = include_mcp

    @classmethod
    def reset(cls):
        cls.started = threading.Event()
        cls.release = threading.Event()
        cls.agents = []
        cls.calls = []

    def load_or_create_session(self, session_id=None, title=None, surface="", meta=None):
        return SimpleNamespace(id=session_id or "serve:blocking-run", title=title or "", meta=meta or {})

    def make_agent(self, **kwargs):
        agent = SimpleNamespace(cancel_event=threading.Event())

        def cancel():
            agent.cancel_event.set()

        agent.cancel = cancel
        self.agents.append(agent)
        return agent

    def run_prompt(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        self.started.set()
        self.release.wait(5)
        session = kwargs.get("session") or SimpleNamespace(id="serve:blocking-run")
        return SimpleNamespace(
            text="finished",
            session=session,
            trace_id="trace_blocking",
            turn_id="turn_blocking",
            run_id="surface_blocking",
            agent=SimpleNamespace(
                provider=SimpleNamespace(model="served-model"),
                budget=SimpleNamespace(usage=_Usage()),
            ),
        )


class _ApprovalBlockingRunRunner:
    approval_returned = threading.Event()
    calls = []
    agents = []

    def __init__(self, config, include_mcp=True):
        self.config = config
        self.include_mcp = include_mcp

    @classmethod
    def reset(cls):
        cls.approval_returned = threading.Event()
        cls.calls = []
        cls.agents = []

    def load_or_create_session(self, session_id=None, title=None, surface="", meta=None):
        return SimpleNamespace(id=session_id or "serve:approval-run", title=title or "", meta=meta or {})

    def make_agent(self, **kwargs):
        agent = SimpleNamespace(cancel_event=threading.Event(), approver=kwargs.get("approver"))

        def cancel():
            agent.cancel_event.set()

        agent.cancel = cancel
        self.agents.append(agent)
        return agent

    def run_prompt(self, prompt, **kwargs):
        agent = kwargs["agent"]
        approved = bool(agent.approver("Allow shell command?"))
        self.approval_returned.set()
        self.calls.append({"prompt": prompt, "approved": approved, **kwargs})
        session = kwargs.get("session") or SimpleNamespace(id="serve:approval-run")
        return SimpleNamespace(
            text=f"approved={approved}",
            session=session,
            trace_id="trace_approval",
            turn_id="turn_approval",
            run_id="surface_approval",
            agent=SimpleNamespace(
                provider=SimpleNamespace(model="served-model"),
                budget=SimpleNamespace(usage=_Usage()),
            ),
        )


def test_server_run_echoes_hermes_session_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingRunRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["api_key"] = "serve-secret"
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_headers, create_data = _request_with_headers(
            port,
            "POST",
            "/v1/runs",
            {"input": "slow run", "session_id": "serve:run-key"},
            headers={
                "Authorization": "Bearer serve-secret",
                "X-Hermes-Session-Key": "gateway:user-42",
            },
        )
        assert _BlockingRunRunner.started.wait(2)
    finally:
        _BlockingRunRunner.release.set()
        srv.shutdown()
        srv.server_close()

    body = json.loads(create_data)
    assert create_status == 202
    assert create_headers["X-Hermes-Session-Key"] == "gateway:user-42"
    assert create_headers["X-Hermes-Session-Id"] == "serve:run-key"
    assert body["id"] == body["run_id"]
    assert body["object"] == "hermes.run"
    assert body["status"] == "started"
    assert body["session_key"] == "gateway:user-42"
    assert _BlockingRunRunner.calls[0]["meta"]["gateway_session_key"] == "gateway:user-42"


def test_server_run_lifecycle_caps_active_runs_and_stop_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    _BlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingRunRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["max_concurrent_runs"] = 1
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "slow run",
            "session_id": "serve:blocking-run",
        })
        create_body = json.loads(create_data)
        run_id = create_body["run_id"]
        assert _BlockingRunRunner.started.wait(2)

        list_status, list_data = _request(port, "GET", "/v1/runs")
        second_status, second_data = _request(port, "POST", "/v1/runs", {"input": "second"})
        stop_status, stop_data = _request(port, "POST", f"/v1/runs/{run_id}/stop", {})
        _BlockingRunRunner.release.set()

        final = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
            final = json.loads(get_data)
            if final.get("run", {}).get("status") == "cancelled":
                break
            time.sleep(0.05)
    finally:
        _BlockingRunRunner.release.set()
        srv.shutdown()
        srv.server_close()

    assert create_status == 202
    assert create_body["id"] == run_id
    assert create_body["status"] == "started"
    listed = json.loads(list_data)["data"]
    assert list_status == 200
    assert any(row["id"] == run_id and row["status"] in {"queued", "running"} for row in listed)
    assert second_status == 429
    assert json.loads(second_data)["code"] == "rate_limit_exceeded"
    assert stop_status == 200
    stop_body = json.loads(stop_data)
    assert stop_body["id"] == run_id
    assert stop_body["run_id"] == run_id
    assert stop_body["status"] == "stopping"
    assert _BlockingRunRunner.agents and _BlockingRunRunner.agents[0].cancel_event.is_set()
    assert final["run"]["status"] == "cancelled"
    assert final["run"]["result"] == "finished"


def test_server_run_approval_without_pending_returns_409(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingRunRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "slow run",
            "session_id": "serve:no-approval-run",
        })
        run_id = json.loads(create_data)["run_id"]
        assert _BlockingRunRunner.started.wait(2)
        approval_status, approval_data = _request(
            port,
            "POST",
            f"/v1/runs/{run_id}/approval",
            {"choice": "once"},
        )
    finally:
        _BlockingRunRunner.release.set()
        srv.shutdown()
        srv.server_close()

    body = json.loads(approval_data)
    assert create_status == 202
    assert approval_status == 409
    assert body["run_id"] == run_id
    assert body["error"]["code"] == "approval_not_pending"


def test_server_created_run_persists_across_handler_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingRunRunner)
    cfg = Config.load()
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "durable run",
            "session_id": "serve:persist-run",
        })
        create_body = json.loads(create_data)
        run_id = create_body["run_id"]
        assert _BlockingRunRunner.started.wait(2)
        _BlockingRunRunner.release.set()

        final = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
            final = json.loads(get_data)
            if final.get("run", {}).get("status") == "completed":
                break
            time.sleep(0.05)
    finally:
        _BlockingRunRunner.release.set()
        srv.shutdown()
        srv.server_close()

    assert create_status == 202
    assert create_body["id"] == run_id
    assert create_body["status"] == "started"
    assert final["run"]["id"] == run_id
    assert final["run"]["status"] == "completed"
    assert final["run_id"] == run_id
    assert final["object"] == "hermes.run"
    assert final["status"] == "completed"
    assert final["output"] == "finished"

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
        list_status, list_data = _request(port, "GET", "/v1/runs")
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
        sse_status, sse_headers, sse_data = _request_with_headers(
            port,
            "GET",
            f"/v1/runs/{run_id}/events",
            headers={"Accept": "text/event-stream"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    run = json.loads(get_data)["run"]
    listed = json.loads(list_data)["data"]
    events = json.loads(events_data)
    assert get_status == 200
    assert run["id"] == run_id
    assert run["run_id"] == run_id
    assert run["object"] == "hermes.run"
    assert run["status"] == "completed"
    assert run["session_id"] == "serve:persist-run"
    assert run["result"] == "finished"
    assert run["output"] == "finished"
    assert run["trace_id"] == "trace_blocking"
    assert run["surface_run_id"] == "surface_blocking"
    assert list_status == 200
    assert any(row["id"] == run_id and row["status"] == "completed" for row in listed)
    assert events_status == 200
    assert events["ok"] is True
    assert events["run"]["id"] == run_id
    event_types = [event.get("type") for event in events["events"]]
    assert event_types[:2] == ["run.queued", "run.running"]
    assert "run.completed" in event_types
    assert sse_status == 200
    assert "text/event-stream" in sse_headers["Content-Type"]
    sse_events = _sse_events(sse_data)
    replayed_types = [
        payload.get("type")
        for event_name, payload in sse_events
        if event_name == "event" and isinstance(payload, dict)
    ]
    assert replayed_types[:2] == ["run.queued", "run.running"]
    assert "run.completed" in replayed_types
    assert sse_events[-2][0] == "done"
    assert sse_events[-2][1]["id"] == run_id
    assert sse_events[-1] == ("done", "[DONE]")


def test_server_startup_marks_stale_api_runs_interrupted(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.server import make_handler

    run = RunStore().start(
        surface="serve",
        kind="serve",
        title="stale api run",
        session_id="serve:stale-run",
        prompt="resume me",
        data={
            "api": "runs",
            "object": "run",
            "server_run_id": "placeholder",
            "created_at": 123,
            "last_event": "run.running",
        },
    )
    stored = RunStore().get(run["id"])
    stored["data"]["server_run_id"] = run["id"]
    RunStore().write(stored)

    srv, port = _serve(make_handler(Config.load()))
    try:
        get_status, get_data = _request(port, "GET", f"/v1/runs/{run['id']}")
    finally:
        srv.shutdown()
        srv.server_close()

    saved = RunStore().get(run["id"])
    body = json.loads(get_data)
    assert saved["status"] == "interrupted"
    assert "API server restarted" in saved["error"]
    assert saved["data"]["interrupted_by_server_start"] is True
    assert saved["data"]["last_event"] == "run.interrupted"
    assert get_status == 200
    assert body["run_id"] == run["id"]
    assert body["object"] == "hermes.run"
    assert body["status"] == "interrupted"
    assert body["run"]["id"] == run["id"]
    assert body["run"]["status"] == "interrupted"
    assert body["run"]["last_event"] == "run.interrupted"


def test_server_restart_closes_pending_run_approval_events(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    _ApprovalBlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _ApprovalBlockingRunRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["approval_timeout_seconds"] = 3600
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "needs restart approval recovery",
            "session_id": "serve:approval-restart-run",
        })
        run_id = json.loads(create_data)["run_id"]

        pending = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            pending_status, pending_data = _request(port, "GET", f"/v1/runs/{run_id}/approval")
            pending = json.loads(pending_data)
            if pending.get("pending"):
                break
            time.sleep(0.05)
    finally:
        srv.shutdown()
        srv.server_close()

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        detail_status, detail_data = _request(port, "GET", f"/v1/runs/{run_id}")
        approval_status, approval_data = _request(port, "GET", f"/v1/runs/{run_id}/approval")
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
        sse_status, sse_headers, sse_data = _request_with_headers(
            port,
            "GET",
            f"/v1/runs/{run_id}/events",
            headers={"Accept": "text/event-stream"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    detail = json.loads(detail_data)
    approval = json.loads(approval_data)
    events = json.loads(events_data)["events"]
    event_types = [event.get("type") for event in events]
    responded = next(event for event in events if event.get("type") == "approval.responded")
    interrupted = next(event for event in events if event.get("type") == "run.interrupted")
    sse_events = _sse_events(sse_data)
    sse_event_types = [
        payload.get("type")
        for name, payload in sse_events
        if name == "event" and isinstance(payload, dict)
    ]

    assert create_status == 202
    assert pending_status == 200
    assert pending["pending"] and pending["pending"][0]["prompt"] == "Allow shell command?"
    assert detail_status == 200
    assert detail["status"] == "interrupted"
    assert detail["run"]["status"] == "interrupted"
    assert detail["run"]["last_event"] == "run.interrupted"
    assert approval_status == 200
    assert approval["pending"] == []
    assert events_status == 200
    assert event_types[:3] == ["run.queued", "run.running", "approval.request"]
    assert "approval.responded" in event_types
    assert "run.interrupted" in event_types
    assert responded["approval_id"] == pending["pending"][0]["id"]
    assert responded["approved"] is False
    assert responded["cancelled"] is True
    assert responded["choice"] == "deny"
    assert "restarted" in responded["reason"]
    assert interrupted["status"] == "interrupted"
    assert "restarted" in interrupted["reason"]
    assert sse_status == 200
    assert "text/event-stream" in sse_headers["Content-Type"]
    assert "approval.responded" in sse_event_types
    assert "run.interrupted" in sse_event_types
    assert sse_events[-2][0] == "done"
    assert sse_events[-2][1]["status"] == "interrupted"
    assert sse_events[-1] == ("done", "[DONE]")


def test_server_run_approval_choice_unblocks_pending_run(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    _ApprovalBlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _ApprovalBlockingRunRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["approval_timeout_seconds"] = 60
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "needs approval",
            "session_id": "serve:approval-choice-run",
        })
        run_id = json.loads(create_data)["run_id"]

        pending = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            pending_status, pending_data = _request(port, "GET", f"/v1/runs/{run_id}/approval")
            pending = json.loads(pending_data)
            if pending.get("pending"):
                break
            time.sleep(0.05)
        approval_status, approval_data = _request(
            port,
            "POST",
            f"/v1/runs/{run_id}/approval",
            {"choice": "approve", "resolve_all": True},
        )

        final = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
            final = json.loads(get_data)
            if final.get("run", {}).get("status") == "completed":
                break
            time.sleep(0.05)
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
    finally:
        srv.shutdown()
        srv.server_close()

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        restart_events_status, restart_events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
    finally:
        srv.shutdown()
        srv.server_close()

    approval = json.loads(approval_data)
    events = json.loads(events_data)["events"]
    restart_events = json.loads(restart_events_data)["events"]
    event_types = [event.get("type") for event in events]
    restart_event_types = [event.get("type") for event in restart_events]
    assert create_status == 202
    assert pending_status == 200
    assert pending["pending"] and pending["pending"][0]["prompt"] == "Allow shell command?"
    assert approval_status == 200
    assert approval["object"] == "hermes.run.approval_response"
    assert approval["run_id"] == run_id
    assert approval["choice"] == "once"
    assert approval["approved"] is True
    assert approval["resolved"] == 1
    assert approval["approval_ids"] == [pending["pending"][0]["id"]]
    assert _ApprovalBlockingRunRunner.approval_returned.wait(0.1)
    assert _ApprovalBlockingRunRunner.calls[0]["approved"] is True
    assert final["run"]["status"] == "completed"
    assert final["output"] == "approved=True"
    assert events_status == 200
    assert "approval.request" in event_types
    responded = next(event for event in events if event.get("type") == "approval.responded")
    assert responded["approval_id"] == pending["pending"][0]["id"]
    assert responded["approved"] is True
    assert responded["choice"] == "once"
    assert restart_events_status == 200
    assert "approval.request" in restart_event_types
    restart_responded = next(event for event in restart_events if event.get("type") == "approval.responded")
    assert restart_responded["approval_id"] == pending["pending"][0]["id"]
    assert restart_responded["approved"] is True
    assert restart_responded["choice"] == "once"


def test_server_stop_releases_pending_approval_waiter(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    _ApprovalBlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _ApprovalBlockingRunRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["approval_timeout_seconds"] = 60
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "needs approval",
            "session_id": "serve:approval-run",
        })
        create_body = json.loads(create_data)
        run_id = create_body["run_id"]

        pending = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            pending_status, pending_data = _request(port, "GET", f"/v1/runs/{run_id}/approval")
            pending = json.loads(pending_data)
            if pending.get("pending"):
                break
            time.sleep(0.05)
        stop_status, stop_data = _request(port, "POST", f"/v1/runs/{run_id}/stop", {})

        final = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
            final = json.loads(get_data)
            if final.get("run", {}).get("status") == "cancelled":
                break
            time.sleep(0.05)
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
    finally:
        srv.shutdown()
        srv.server_close()

    events = json.loads(events_data)["events"]
    event_types = [event.get("type") for event in events]
    assert create_status == 202
    assert create_body["id"] == run_id
    assert create_body["status"] == "started"
    assert pending_status == 200
    assert pending["pending"] and pending["pending"][0]["prompt"] == "Allow shell command?"
    assert stop_status == 200
    stop_body = json.loads(stop_data)
    assert stop_body["run_id"] == run_id
    assert stop_body["status"] == "stopping"
    assert _ApprovalBlockingRunRunner.approval_returned.wait(0.1)
    assert _ApprovalBlockingRunRunner.calls[0]["approved"] is False
    assert _ApprovalBlockingRunRunner.agents[0].cancel_event.is_set()
    assert final["run"]["status"] == "cancelled"
    assert final["run"]["result"] == "approved=False"
    assert events_status == 200
    assert "approval.request" in event_types
    assert "run.stopping" in event_types
    denied = [event for event in events if event.get("type") == "approval.responded"][-1]
    assert denied["approved"] is False
    assert denied["cancelled"] is True


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
    listed = json.loads(list_data)
    assert any(row["id"] == job_id for row in listed["data"])
    assert any(row["id"] == job_id for row in listed["jobs"])
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
