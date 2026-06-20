"""OpenAI-compatible HTTP API surface."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
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


class _LateAccessResult:
    accessed = threading.Event()

    @classmethod
    def reset(cls):
        cls.accessed = threading.Event()

    @property
    def text(self):
        self.accessed.set()
        return "late"

    @property
    def session(self):
        self.accessed.set()
        return SimpleNamespace(id="serve:late-result")

    @property
    def trace_id(self):
        self.accessed.set()
        return "trace_late_result"

    @property
    def turn_id(self):
        self.accessed.set()
        return "turn_late_result"

    @property
    def run_id(self):
        self.accessed.set()
        return "run_late_result"

    @property
    def agent(self):
        self.accessed.set()
        return SimpleNamespace(
            provider=SimpleNamespace(model="served-model"),
            budget=SimpleNamespace(usage=_Usage()),
        )

    @property
    def usage(self):
        self.accessed.set()
        return _Usage()


class _LateAccessStreamingRunner:
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
        _LateAccessResult.reset()

    def load_or_create_session(self, session_id=None, title=None, history=None, surface="", meta=None):
        return SimpleNamespace(
            id=session_id or "serve:late-stream",
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
        return _LateAccessResult()


class _ClearingCancelRunner:
    started = threading.Event()
    cleared = threading.Event()
    release = threading.Event()
    agents = []
    cancel_seen_before_clear = False

    def __init__(self, config, include_mcp=True):
        self.config = config
        self.include_mcp = include_mcp

    @classmethod
    def reset(cls):
        cls.started = threading.Event()
        cls.cleared = threading.Event()
        cls.release = threading.Event()
        cls.agents = []
        cls.cancel_seen_before_clear = False

    def load_or_create_session(self, session_id=None, title=None, history=None, surface="", meta=None):
        return SimpleNamespace(
            id=session_id or "serve:clear-cancel",
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
        self.started.set()
        agent = kwargs.get("agent") or (self.agents[-1] if self.agents else None)
        deadline = time.monotonic() + 1
        while agent is not None and time.monotonic() < deadline:
            if agent.cancel_event.is_set():
                self.__class__.cancel_seen_before_clear = True
                break
            time.sleep(0.01)
        if agent is not None:
            agent.cancel_event.clear()
        self.cleared.set()
        self.release.wait(5)
        session = kwargs.get("session") or SimpleNamespace(id="serve:clear-cancel")
        return SimpleNamespace(
            text="late",
            session=session,
            trace_id="trace_clear_cancel",
            turn_id="turn_clear_cancel",
            run_id="run_clear_cancel",
            agent=agent or SimpleNamespace(
                provider=SimpleNamespace(model="served-model"),
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
        retrieve_status, retrieve_data = _request(port, "GET", "/v1/models/local-active-model")
        missing_status, missing_data = _request(port, "GET", "/v1/models/does-not-exist")
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
    assert retrieve_status == 200
    retrieved = json.loads(retrieve_data)
    assert retrieved["object"] == "model"
    assert retrieved["id"] == "local-active-model"
    assert retrieved["owned_by"] == "localtest"
    assert missing_status == 404
    assert json.loads(missing_data)["error"]["code"] == "model_not_found"


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
        health_status, health_data = _request(port, "GET", "/v1/health")
        detailed_health_status, _detailed_health_data = _request(port, "GET", "/v1/health/detailed")
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
    assert health_status == 200
    assert json.loads(health_data)["ok"] is True
    assert detailed_health_status == 401
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
            "max_completion_tokens": 123,
            "reasoning_effort": "high",
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
    assert call["meta"]["runtime_controls"]["reasoning_effort"] == "high"
    assert call["max_tokens"] == 123


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


def test_openai_chat_completions_rejects_invalid_max_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/chat/completions", {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 0,
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    err = json.loads(data)["error"]
    assert err["code"] == "invalid_max_tokens"
    assert err["param"] == "max_tokens"
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
    assert unsupported["code"] == "unsupported_content_type"
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


def test_openai_aiohttp_body_limit_uses_json_security_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    monkeypatch.setattr(server, "_MAX_BODY_BYTES", 8)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["cors_origins"] = ["http://client.local"]

    async def exercise() -> tuple[int, dict, str]:
        from aiohttp import ClientSession, web

        app = server.make_app(cfg)
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
                    data=b'{"messages":[]}',
                    headers={"Content-Type": "application/json", "Origin": "http://client.local"},
                ) as resp:
                    return resp.status, dict(resp.headers), await resp.text()
        finally:
            await runner.cleanup()

    status, headers, data = asyncio.run(exercise())

    assert status == 413
    assert json.loads(data)["error"] == "request body too large"
    assert headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Access-Control-Allow-Origin"] == "http://client.local"


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
            "max_tokens": 88,
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
    assert _FakeRunner.calls[0]["max_tokens"] == 88


def test_chat_completions_aiohttp_stream_disconnect_cancels_live_agent(monkeypatch, tmp_path):
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
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "stream": True,
                        "messages": [{"role": "user", "content": "disconnect me"}],
                        "session_id": "serve:chat-disconnect",
                    },
                ) as resp:
                    assert resp.status == 200
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    blank_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert data_line.startswith(b"data: ")
                    assert blank_line == b"\n"
                    assert await asyncio.to_thread(_BlockingResponsesRunner.started.wait, 1)
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


def test_chat_completions_disconnect_drops_late_stream_result(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _LateAccessStreamingRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _LateAccessStreamingRunner)

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
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "stream": True,
                        "messages": [{"role": "user", "content": "disconnect me"}],
                        "session_id": "serve:chat-late-drop",
                    },
                ) as resp:
                    assert resp.status == 200
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    blank_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert data_line.startswith(b"data: ")
                    assert blank_line == b"\n"
                    assert await asyncio.to_thread(_LateAccessStreamingRunner.started.wait, 1)
                    resp.close()
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if (
                            _LateAccessStreamingRunner.agents
                            and _LateAccessStreamingRunner.agents[0].cancel_event.is_set()
                        ):
                            break
                        await asyncio.sleep(0.05)
                    _LateAccessStreamingRunner.release.set()
                    return True
        finally:
            _LateAccessStreamingRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True
    assert not _LateAccessResult.accessed.is_set()


def test_chat_completions_disconnect_reapplies_cancel_after_run_start_clear(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _ClearingCancelRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _ClearingCancelRunner)

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
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "stream": True,
                        "messages": [{"role": "user", "content": "race cancel"}],
                        "session_id": "serve:chat-cancel-reapply",
                    },
                ) as resp:
                    assert resp.status == 200
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    blank_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert data_line.startswith(b"data: ")
                    assert blank_line == b"\n"
                    assert await asyncio.to_thread(_ClearingCancelRunner.started.wait, 1)
                    resp.close()
                    assert await asyncio.to_thread(_ClearingCancelRunner.cleared.wait, 2)
                    if not _ClearingCancelRunner.cancel_seen_before_clear:
                        return False
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if (
                            _ClearingCancelRunner.agents
                            and _ClearingCancelRunner.agents[0].cancel_event.is_set()
                        ):
                            return True
                        await asyncio.sleep(0.05)
                    return False
        finally:
            _ClearingCancelRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True


def test_chat_completions_nonstream_disconnect_cancels_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _LateAccessStreamingRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _LateAccessStreamingRunner)

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
                request_task = asyncio.ensure_future(session.post(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "disconnect me"}],
                        "session_id": "serve:chat-nonstream-disconnect",
                    },
                ))
                assert await asyncio.to_thread(_LateAccessStreamingRunner.started.wait, 1)
                request_task.cancel()
                try:
                    response = await request_task
                except (asyncio.CancelledError, Exception):
                    response = None
                if response is not None:
                    response.close()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if (
                        _LateAccessStreamingRunner.agents
                        and _LateAccessStreamingRunner.agents[0].cancel_event.is_set()
                    ):
                        return True
                    await asyncio.sleep(0.05)
                return False
        finally:
            _LateAccessStreamingRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True
    assert not _LateAccessResult.accessed.is_set()


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
    assert routes["models.retrieve"]["path"] == "/v1/models/{model_id}"
    assert routes["responses"]["path"] == "/v1/responses"
    assert routes["responses"]["streaming"] is True
    assert routes["responses.input_items"]["path"] == "/v1/responses/{response_id}/input_items"
    assert routes["responses.input_tokens"]["path"] == "/v1/responses/input_tokens"
    assert routes["responses.compact"]["path"] == "/v1/responses/compact"
    assert routes["health"]["path"] == "/v1/health"
    assert routes["health.detailed"]["path"] == "/v1/health/detailed"
    assert routes["skills"]["path"] == "/v1/skills"
    assert routes["toolsets"]["path"] == "/v1/toolsets"
    assert routes["jobs"]["path"] == "/api/jobs"
    assert routes["jobs.detail"]["methods"] == ["GET", "PATCH", "DELETE"]
    assert routes["jobs.control"]["methods"] == ["POST"]
    assert routes["session_checks"]["path"] == "/api/session-checks"
    assert routes["session_checks.repair"]["path"] == "/api/session-checks/repair"
    assert caps["features"]["response_input_items"] is True
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
        plain_options_status, plain_options_headers, _plain_options_data = _request_with_headers(
            port,
            "OPTIONS",
            "/v1/chat/completions",
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
    skills = json.loads(skills_data)
    assert skills["object"] == "list"
    assert skills["count"] == len(skills["data"])
    assert skills["data"]
    assert all(row["object"] == "skill" and row["id"] == row["name"] for row in skills["data"])
    assert all("directory" in row and "toolsets" in row for row in skills["data"])
    assert toolsets_status == 200
    toolsets = json.loads(toolsets_data)
    assert toolsets["object"] == "list"
    assert toolsets["count"] == len(toolsets["data"])
    assert toolsets["active"]
    assert all(row["object"] == "toolset" and row["id"] == row["name"] for row in toolsets["data"])
    assert all("tool_count" in row and "enabled_count" in row for row in toolsets["data"])
    assert options_status == 204
    assert options_headers["Access-Control-Allow-Origin"] == "http://client.local"
    assert options_headers["Access-Control-Max-Age"] == "600"
    assert plain_options_status == 204
    assert plain_options_headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in plain_options_headers
    assert blocked_status == 403
    assert json.loads(blocked_data)["error"] == "cors origin not allowed"


def test_server_detailed_health_summarizes_cron_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.cron import CronStore

    store = CronStore()
    running = store.add("every 1h", "running job", name="running")
    failing = store.add("every 2h", "failing job", name="failing")
    store.mark_running(running.id)
    store.record_run(failing.id, time.time() - 60, ok=False, error="boom")

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "GET", "/v1/health/detailed")
    finally:
        srv.shutdown()
        srv.server_close()

    jobs = json.loads(data)["stores"]["jobs"]

    assert status == 200
    assert jobs["count"] == 2
    assert jobs["enabled"] == 2
    assert jobs["states"]["running"] == 1
    assert jobs["states"]["error"] == 1
    assert jobs["running"] == 1
    assert jobs["errors"] == 1
    assert jobs["last_error_count"] == 1
    assert jobs["error_job_ids"] == [failing.id]


def test_server_session_checks_report_and_repair_stale_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.runs import RunStore
    from aegis.session import Session, SessionStore

    store = SessionStore()
    runs = RunStore()
    session = Session(id="api-cross-session", title="api cross session")
    store.save(session)
    run = runs.start(surface="api", kind="chat", session_id=session.id, prompt="stale")
    run["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    runs.write(run)

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(
            port,
            "GET",
            "/api/session-checks?session_limit=10&run_limit=10&stale_running_seconds=0",
        )
        repair_status, repair_data = _request(
            port,
            "POST",
            "/api/harness/cross-session/repair",
            {
                "session_limit": 10,
                "run_limit": 10,
                "stale_running_seconds": 0,
                "resume_reason": "api_repair",
            },
        )
        alias_status, alias_data = _request(
            port,
            "POST",
            "/api/session-checks",
            {"action": "report", "session_limit": 10, "run_limit": 10},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    body = json.loads(data)
    repair = json.loads(repair_data)
    alias = json.loads(alias_data)

    assert status == 200
    assert body["object"] == "hermes.cross_session_integrity_report"
    assert "generated_at" in body
    assert "stale_running_run" in {issue["code"] for issue in body["issues"]}
    assert repair_status == 200
    assert repair["object"] == "hermes.cross_session_integrity_repair_result"
    assert repair["repair"]["repaired_running_runs"] == 1
    assert repair["repair"]["marked_resume_pending"] == 1
    assert repair["repair"]["object"] == "hermes.cross_session_integrity_repair"
    assert repair["report"]["object"] == "hermes.cross_session_integrity_report"
    assert "stale_running_run" not in {issue["code"] for issue in repair["report"]["issues"]}
    assert runs.get(run["id"])["status"] == "interrupted"
    assert store.load(session.id).meta["resume_reason"] == "api_repair"
    assert alias_status == 200
    assert alias["object"] == "hermes.cross_session_integrity_report"


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
            "max_output_tokens": 77,
            "reasoning": {"effort": "low"},
            "include": ["output[*].content", "reasoning.encrypted_content"],
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
    assert body["include"] == ["output[*].content", "reasoning.encrypted_content"]
    assert body["previous_response_id"] is None
    assert body["metadata"]["session_id"] == "serve:responses"
    assert body["metadata"]["reasoning_effort"] == "low"
    assert body["usage"]["input_tokens"] == 11
    assert body["usage"]["output_tokens"] == 7
    assert body["usage"]["total_tokens"] == 18
    assert body["usage"]["prompt_tokens"] == 11
    assert body["usage"]["completion_tokens"] == 7
    assert body["usage"]["input_tokens_details"]["cached_tokens"] == 3
    assert get_status == 200
    retrieved = json.loads(get_data)
    assert retrieved["id"] == response_id
    assert retrieved["include"] == ["output[*].content", "reasoning.encrypted_content"]
    assert cancel_status == 200
    cancelled = json.loads(cancel_data)
    assert cancelled["status"] == "cancelled"
    assert cancelled["include"] == ["output[*].content", "reasoning.encrypted_content"]
    assert delete_status == 200
    deleted = json.loads(delete_data)
    assert deleted["ok"] is True
    assert deleted["id"] == response_id
    assert deleted["object"] == "response"
    assert deleted["deleted"] is True
    assert _FakeRunner.calls[0]["session_id"] == "serve:responses"
    assert _FakeRunner.calls[0]["meta"]["runtime_controls"]["reasoning_effort"] == "low"
    assert _FakeRunner.calls[0]["max_tokens"] == 77


def test_responses_preserves_parallel_tool_calls_false(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "hello",
            "parallel_tool_calls": False,
        })
        body = json.loads(data)
        response_id = body["id"]
        get_status, get_data = _request(port, "GET", f"/v1/responses/{response_id}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert body["parallel_tool_calls"] is False
    assert get_status == 200
    assert json.loads(get_data)["parallel_tool_calls"] is False


def test_responses_usage_accepts_dict_payloads(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class DictUsageRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            return SimpleNamespace(
                text="counted",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "input_tokens_details": {"cached_tokens": 9},
                },
                session=SimpleNamespace(id=kwargs.get("session_id") or "serve:dict-usage"),
                trace_id="trace_dict_usage",
                turn_id="turn_dict_usage",
                run_id="run_dict_usage",
                agent=SimpleNamespace(provider=SimpleNamespace(model="served-model")),
            )

    monkeypatch.setattr(server, "SurfaceRunner", DictUsageRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {"input": "hello"})
    finally:
        srv.shutdown()
        srv.server_close()

    body = json.loads(data)
    assert status == 200
    assert body["usage"]["input_tokens"] == 100
    assert body["usage"]["output_tokens"] == 50
    assert body["usage"]["total_tokens"] == 150
    assert body["usage"]["prompt_tokens"] == 100
    assert body["usage"]["completion_tokens"] == 50
    assert body["usage"]["input_tokens_details"]["cached_tokens"] == 9


def test_responses_rejects_invalid_reasoning_effort(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "hello",
            "reasoning": {"effort": "loud"},
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    err = json.loads(data)["error"]
    assert err["code"] == "invalid_reasoning_effort"
    assert err["param"] == "reasoning.effort"
    assert _FakeRunner.calls == []


def test_responses_accepts_reasoning_none_as_off(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "hello",
            "reasoning": {"effort": "none"},
        })
    finally:
        srv.shutdown()
        srv.server_close()

    body = json.loads(data)
    assert status == 200
    assert body["metadata"]["reasoning_effort"] == "off"
    assert _FakeRunner.calls[0]["meta"]["runtime_controls"]["reasoning_effort"] == "off"


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


def test_responses_rejects_invalid_include(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        object_status, object_data = _request(port, "POST", "/v1/responses", {
            "input": "hello",
            "include": {"path": "output[*].content"},
        })
        item_status, item_data = _request(port, "POST", "/v1/responses", {
            "input": "hello",
            "include": ["output[*].content", {"path": "bad"}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert object_status == item_status == 400
    object_error = json.loads(object_data)["error"]
    item_error = json.loads(item_data)["error"]
    assert object_error["code"] == "invalid_include"
    assert object_error["param"] == "include"
    assert item_error["code"] == "invalid_include"
    assert item_error["param"] == "include[1]"
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


def test_responses_accepts_output_text_content_part(monkeypatch, tmp_path):
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
                "content": [{"type": "output_text", "text": "reuse prior output text"}],
            }],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert json.loads(data)["object"] == "response"
    assert _FakeRunner.calls[0]["prompt"].content == "reuse prior output text"
    assert items_status == 200
    assert json.loads(items_data)["data"][0]["content"] == [
        {"type": "input_text", "text": "reuse prior output text"}
    ]


def test_responses_preserves_text_part_annotations_in_input_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    annotations = [{"type": "file_citation", "file_id": "file_123", "index": 0}]
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [{
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "cited output",
                    "annotations": annotations,
                }],
            }, {
                "role": "user",
                "content": "continue",
            }],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["history"][0].content == "cited output"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["content"] == [{
        "type": "output_text",
        "text": "cited output",
        "annotations": annotations,
    }]


def test_responses_accepts_refusal_content_part_as_assistant_history(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [{
                "role": "assistant",
                "content": [{"type": "refusal", "refusal": "I cannot help with that."}],
            }, {
                "role": "user",
                "content": "try a safe version",
            }],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "try a safe version"
    assert [(m.role, m.content) for m in call["history"]] == [
        ("assistant", "I cannot help with that."),
    ]
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["content"] == [{"type": "refusal", "refusal": "I cannot help with that."}]


def test_responses_preserves_assistant_output_text_input_item(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "continue"},
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["history"][0].role == "assistant"
    assert _FakeRunner.calls[0]["history"][0].content == "prior answer"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["role"] == "assistant"
    assert items[0]["content"] == [{"type": "output_text", "text": "prior answer"}]
    assert items[1]["role"] == "user"
    assert items[1]["content"] == [{"type": "input_text", "text": "continue"}]


def test_responses_rejects_invalid_image_url(monkeypatch, tmp_path):
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
                "content": [{"type": "input_image", "image_url": "ftp://example.test/image.png"}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "invalid_image_url"
    assert error["param"] == "input[0].content[0].image_url"
    assert _FakeRunner.calls == []


def test_responses_accepts_input_file_parts_as_document_references(monkeypatch, tmp_path):
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
                "content": [
                    {"type": "input_text", "text": "review this"},
                    {"type": "input_file", "file_id": "file_123", "filename": "brief.pdf"},
                    {
                        "type": "input_file",
                        "file_url": "https://example.test/brief.pdf",
                        "detail": "high",
                    },
                    {
                        "type": "document",
                        "document": {"id": "doc_123", "filename": "brief-notes.pdf"},
                    },
                ],
            }],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["prompt"].content == (
        "review this\n[file: file_123]\n[file: https://example.test/brief.pdf]\n[file: doc_123]"
    )
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["content"] == [
        {"type": "input_text", "text": "review this"},
        {"type": "input_file", "file_id": "file_123", "filename": "brief.pdf"},
        {"type": "input_file", "file_url": "https://example.test/brief.pdf", "detail": "high"},
        {"type": "input_file", "file_id": "doc_123", "filename": "brief-notes.pdf"},
    ]


def test_responses_groups_top_level_typed_content_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    image_url = "data:image/png;base64,XYZ"
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"type": "input_text", "text": "review this"},
                {"type": "input_image", "image_url": image_url},
                {"type": "input_file", "file_id": "file_123", "filename": "brief.pdf"},
                {"type": "document", "document": {"url": "https://example.test/notes.md"}},
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "review this\n[file: file_123]\n[file: https://example.test/notes.md]"
    assert call["prompt"].images == [image_url]
    assert call["history"] == []
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert len(items) == 1
    assert items[0]["role"] == "user"
    assert items[0]["content"] == [
        {"type": "input_text", "text": "review this"},
        {"type": "input_image", "image_url": image_url},
        {"type": "input_file", "file_id": "file_123", "filename": "brief.pdf"},
        {"type": "input_file", "file_url": "https://example.test/notes.md"},
    ]


def test_responses_accepts_input_audio_parts_as_audio_references(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    audio_part = {
        "type": "input_audio",
        "input_audio": {"data": "UklGRg==", "format": "wav"},
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "listen to this"},
                    audio_part,
                ],
            }],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["prompt"].content == "listen to this\n[audio: inline audio (wav)]"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["content"] == [
        {"type": "input_text", "text": "listen to this"},
        audio_part,
    ]


def test_responses_groups_top_level_audio_content_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    audio_part = {
        "type": "input_audio",
        "data": "UklGRg==",
        "format": "mp3",
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"type": "input_text", "text": "summarize this"},
                audio_part,
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["prompt"].content == "summarize this\n[audio: inline audio (mp3)]"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["role"] == "user"
    assert items[0]["content"] == [
        {"type": "input_text", "text": "summarize this"},
        {"type": "input_audio", "input_audio": {"data": "UklGRg==", "format": "mp3"}},
    ]


def test_responses_rejects_malformed_input_file_parts(monkeypatch, tmp_path):
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
                "content": [{"type": "document", "document": {}}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "invalid_file_content"
    assert error["param"] == "input[0].content[0].file_id"
    assert _FakeRunner.calls == []


def test_responses_rejects_malformed_input_audio_parts(monkeypatch, tmp_path):
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
                "content": [{"type": "input_audio"}],
            }],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "invalid_audio_content"
    assert error["param"] == "input[0].content[0].input_audio"
    assert _FakeRunner.calls == []


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
                "content": [{"type": "widget", "widget": {"id": "widget_123"}}],
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
        chained_id = json.loads(chained_data)["id"]
        chained_items_status, chained_items_data = _request(
            port2,
            "GET",
            f"/v1/responses/{chained_id}/input_items?order=asc",
        )
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
    assert [m.content for m in second_call["history"][:2]] == [
        "first",
        "hello",
    ]
    assert second_call["prompt"].content == "second"
    assert chained_items_status == 200
    chained_items = json.loads(chained_items_data)["data"]
    assert [row["role"] for row in chained_items] == ["user", "assistant", "user"]
    assert all(row["role"] != "system" for row in chained_items)


def test_responses_conversation_store_false_stays_stateless(monkeypatch, tmp_path):
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
            "store": False,
        })
        first_id = json.loads(first_data)["id"]
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "input": "second",
            "conversation": "thread-a",
            "store": False,
        })
        second_id = json.loads(second_data)["id"]
        first_get_status, _first_get_data = _request(port, "GET", f"/v1/responses/{first_id}")
        second_get_status, _second_get_data = _request(port, "GET", f"/v1/responses/{second_id}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == 200
    assert second_status == 200
    assert first_get_status == 404
    assert second_get_status == 404
    assert json.loads(first_data)["previous_response_id"] is None
    assert json.loads(second_data)["previous_response_id"] is None
    assert _FakeRunner.calls[0]["history"] == []
    assert _FakeRunner.calls[1]["history"] == []


def test_responses_input_items_persist_paginate_and_replay(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"role": "user", "content": "first"},
                {"role": "user", "content": "second"},
            ],
            "instructions": "be direct",
            "metadata": {"session_id": "serve:input-items"},
        })
        response_id = json.loads(data)["id"]
        first_status, first_page = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?order=asc&limit=2",
        )
        first_body = json.loads(first_page)
        second_status, second_page = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?order=asc&after={first_body['last_id']}",
        )
        before_status, before_page = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?order=asc&before={first_body['last_id']}",
        )
        desc_status, desc_page = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?order=desc&limit=2",
        )
        invalid_limit_status, invalid_limit_data = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?limit=abc",
        )
        invalid_order_status, invalid_order_data = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?order=sideways",
        )
        missing_status, missing_data = _request(port, "GET", "/v1/responses/resp_missing/input_items")
    finally:
        srv.shutdown()
        srv.server_close()

    srv2, port2 = _serve(server.make_handler(Config.load()))
    try:
        replay_status, replay_data = _request(port2, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv2.shutdown()
        srv2.server_close()

    assert status == 200
    assert first_status == 200
    assert second_status == 200
    assert before_status == 200
    assert desc_status == 200
    assert missing_status == 404
    assert "response not found" in json.loads(missing_data)["error"]
    assert first_body["object"] == "list"
    assert first_body["response_id"] == response_id
    assert first_body["limit"] == 2
    assert first_body["order"] == "asc"
    assert first_body["total_count"] == 3
    assert first_body["has_more"] is True
    assert [row["role"] for row in first_body["data"]] == ["system", "user"]
    assert first_body["data"][0]["object"] == "response.input_item"
    assert first_body["data"][0]["response_id"] == response_id
    assert first_body["data"][0]["content"][0]["text"] == "be direct"
    second_body = json.loads(second_page)
    assert second_body["has_more"] is False
    assert [row["content"][0]["text"] for row in second_body["data"]] == ["second"]
    before_body = json.loads(before_page)
    assert before_body["has_more"] is False
    assert [row["content"][0]["text"] for row in before_body["data"]] == ["be direct"]
    desc_body = json.loads(desc_page)
    assert desc_body["has_more"] is True
    assert [row["content"][0]["text"] for row in desc_body["data"]] == ["second", "first"]
    assert invalid_limit_status == 400
    assert json.loads(invalid_limit_data)["error"]["code"] == "invalid_limit"
    assert json.loads(invalid_limit_data)["error"]["param"] == "limit"
    assert invalid_order_status == 400
    assert json.loads(invalid_order_data)["error"]["code"] == "invalid_order"
    assert json.loads(invalid_order_data)["error"]["param"] == "order"
    assert replay_status == 200
    replay_body = json.loads(replay_data)
    assert [row["content"][0]["text"] for row in replay_body["data"]] == [
        "be direct",
        "first",
        "second",
    ]


def test_responses_input_items_default_newest_first_limit_20(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    input_items = [
        {"role": "user", "content": f"item-{index:02d}"}
        for index in range(25)
    ]
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {"input": input_items})
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert items_status == 200
    body = json.loads(items_data)
    assert body["has_more"] is True
    assert len(body["data"]) == 20
    assert [row["content"][0]["text"] for row in body["data"][:3]] == [
        "item-24",
        "item-23",
        "item-22",
    ]


def test_responses_input_tokens_counts_previous_response_context(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        simple_status, simple_data = _request(port, "POST", "/v1/responses/input_tokens", {
            "model": "served-model",
            "input": "short",
        })
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "first request",
            "instructions": "be direct",
        })
        response_id = json.loads(first_data)["id"]
        chained_status, chained_data = _request(port, "POST", "/v1/responses/input_tokens", {
            "model": "served-model",
            "input": "second request with tool config",
            "previous_response_id": response_id,
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
        })
        missing_status, missing_data = _request(port, "POST", "/v1/responses/input_tokens", {
            "input": "missing",
            "previous_response_id": "resp_missing",
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert simple_status == 200
    assert first_status == 200
    assert chained_status == 200
    assert json.loads(simple_data)["object"] == "response.input_tokens"
    assert json.loads(simple_data)["input_tokens"] > 0
    assert json.loads(chained_data)["input_tokens"] > json.loads(simple_data)["input_tokens"]
    assert missing_status == 404
    assert "Previous response not found" in json.loads(missing_data)["error"]


def test_responses_compact_returns_chainable_compaction(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        compact_status, compact_data = _request(port, "POST", "/v1/responses/compact", {
            "model": "served-model",
            "conversation": "compact-thread",
            "instructions": "keep the project context",
            "input": [
                {"role": "user", "content": "build the dashboard"},
                {"role": "assistant", "content": "I built the dashboard"},
                {"role": "user", "content": "continue the audit"},
            ],
        })
        compact = json.loads(compact_data)
        compact_id = compact["id"]
        get_status, get_data = _request(port, "GET", f"/v1/responses/{compact_id}")
        chained_status, chained_data = _request(port, "POST", "/v1/responses", {
            "input": "what remains?",
            "previous_response_id": compact_id,
        })
        chained_id = json.loads(chained_data)["id"]
        items_status, items_data = _request(
            port,
            "GET",
            f"/v1/responses/{chained_id}/input_items?order=asc",
        )
        conversation_status, conversation_data = _request(port, "POST", "/v1/responses", {
            "input": "conversation follow-up",
            "conversation": "compact-thread",
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert compact_status == 200
    assert compact["object"] == "response.compaction"
    assert compact["conversation"] == "compact-thread"
    assert compact["usage"]["total_tokens"] >= compact["usage"]["input_tokens"] > 0
    assert [item["type"] for item in compact["output"]][-1] == "compaction"
    assert compact["output"][-1]["encrypted_content"].startswith("aegis-local-compaction:")
    assert get_status == 200
    assert json.loads(get_data)["id"] == compact_id
    assert chained_status == 200
    assert json.loads(chained_data)["previous_response_id"] == compact_id
    chained_history = _FakeRunner.calls[0]["history"]
    assert any("Compaction summary:" in message.content for message in chained_history)
    summary_text = next(message.content for message in chained_history if "Compaction summary:" in message.content)
    assert "assistant: I built the dashboard" in summary_text
    assert "user: continue the audit" in summary_text
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert any(item["type"] == "compaction" for item in items)
    assert any(
        item.get("role") == "assistant"
        and "Compaction summary:" in item.get("content", [{}])[0].get("text", "")
        for item in items
    )
    assert items[-1]["content"][0]["text"] == "what remains?"
    assert conversation_status == 200
    assert json.loads(conversation_data)["previous_response_id"] == compact_id


def test_responses_preserves_message_input_item_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    image_url = "data:image/png;base64,XYZ"
    message_item = {
        "id": "msg_client_1",
        "type": "message",
        "status": "completed",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "look at this"},
            {"type": "input_image", "image_url": {"url": image_url, "detail": "low"}},
        ],
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {"input": [message_item]})
        response_id = json.loads(data)["id"]
    finally:
        srv.shutdown()
        srv.server_close()

    srv2, port2 = _serve(server.make_handler(Config.load()))
    try:
        items_status, items_data = _request(port2, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv2.shutdown()
        srv2.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "look at this"
    assert call["prompt"].images == [image_url]
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert len(items) == 1
    assert items[0]["id"] == "msg_client_1"
    assert items[0]["object"] == "response.input_item"
    assert items[0]["response_id"] == response_id
    assert items[0]["status"] == "completed"
    assert items[0]["role"] == "user"
    assert items[0]["content"] == [
        {"type": "input_text", "text": "look at this"},
        {"type": "input_image", "image_url": image_url, "detail": "low"},
    ]


def test_responses_accepts_input_image_file_id(monkeypatch, tmp_path):
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
                "content": [
                    {"type": "input_text", "text": "inspect stored image"},
                    {"type": "input_image", "file_id": "file_img_123", "detail": "high"},
                ],
            }],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(
            port,
            "GET",
            f"/v1/responses/{response_id}/input_items?order=asc",
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["prompt"].content == "inspect stored image\n[image: file_img_123]"
    assert _FakeRunner.calls[0]["prompt"].images == []
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["content"] == [
        {"type": "input_text", "text": "inspect stored image"},
        {"type": "input_image", "file_id": "file_img_123", "detail": "high"},
    ]


def test_responses_preserves_assistant_message_phase_across_previous_response(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    assistant_item = {
        "id": "msg_assistant_commentary",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "phase": "commentary",
        "content": [{"type": "output_text", "text": "working note"}],
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": [
                assistant_item,
                {"role": "user", "content": "continue from note"},
            ],
        })
        first_id = json.loads(first_data)["id"]
        first_items_status, first_items_data = _request(
            port,
            "GET",
            f"/v1/responses/{first_id}/input_items?order=asc",
        )
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "previous_response_id": first_id,
            "input": "now finish",
        })
        second_id = json.loads(second_data)["id"]
        second_items_status, second_items_data = _request(
            port,
            "GET",
            f"/v1/responses/{second_id}/input_items?order=asc",
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == second_status == 200
    assert first_items_status == second_items_status == 200
    first_items = json.loads(first_items_data)["data"]
    first_assistant = next(item for item in first_items if item.get("id") == "msg_assistant_commentary")
    assert first_assistant["phase"] == "commentary"
    assert first_assistant["content"] == [{"type": "output_text", "text": "working note"}]
    second_items = json.loads(second_items_data)["data"]
    second_assistant = next(item for item in second_items if item.get("id") == "msg_assistant_commentary")
    assert second_assistant["phase"] == "commentary"
    assert second_assistant["content"] == [{"type": "output_text", "text": "working note"}]


def test_responses_rejects_invalid_message_phase(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {
                    "role": "assistant",
                    "phase": "draft",
                    "content": [{"type": "output_text", "text": "bad phase"}],
                },
                {"role": "user", "content": "continue"},
            ],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "invalid_message_phase"
    assert error["param"] == "input[0].phase"
    assert _FakeRunner.calls == []


def test_responses_preserves_opaque_assistant_toolish_input_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    reasoning_item = {
        "id": "rs_client_1",
        "type": "reasoning",
        "status": "completed",
        "summary": [{"type": "summary_text", "text": "prior reasoning"}],
    }
    web_search_item = {
        "id": "ws_client_1",
        "type": "web_search_call",
        "status": "completed",
        "action": {"type": "search", "query": "aegis responses"},
    }
    mcp_item = {
        "id": "mcp_client_1",
        "type": "mcp_call",
        "status": "completed",
        "server_label": "docs",
        "name": "lookup",
        "arguments": {"topic": "responses"},
        "output": [{"type": "text", "text": "result"}],
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                reasoning_item,
                web_search_item,
                mcp_item,
                {"role": "user", "content": "continue"},
            ],
        })
        response_id = json.loads(data)["id"]
    finally:
        srv.shutdown()
        srv.server_close()

    srv2, port2 = _serve(server.make_handler(Config.load()))
    try:
        items_status, items_data = _request(port2, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
        second_status, second_data = _request(port2, "POST", "/v1/responses", {
            "previous_response_id": response_id,
            "input": "follow up",
        })
        second_id = json.loads(second_data)["id"]
        second_items_status, second_items_data = _request(
            port2,
            "GET",
            f"/v1/responses/{second_id}/input_items?order=asc",
        )
    finally:
        srv2.shutdown()
        srv2.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "continue"
    assert call["history"] == []
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert [item["type"] for item in items] == [
        "reasoning",
        "web_search_call",
        "mcp_call",
        "message",
    ]
    assert items[0]["summary"] == reasoning_item["summary"]
    assert items[1]["action"] == web_search_item["action"]
    assert items[2]["output"] == mcp_item["output"]
    assert all(item["response_id"] == response_id for item in items)
    assert second_status == 200
    assert second_items_status == 200
    second_call = _FakeRunner.calls[1]
    assert second_call["prompt"].content == "follow up"
    assert [m.content for m in second_call["history"]] == ["continue", "hello"]
    second_items = json.loads(second_items_data)["data"]
    assert [item["type"] for item in second_items[:3]] == [
        "reasoning",
        "web_search_call",
        "mcp_call",
    ]
    assert second_items[0]["summary"] == reasoning_item["summary"]
    assert second_items[1]["action"] == web_search_item["action"]
    assert second_items[2]["output"] == mcp_item["output"]
    assert all(item["response_id"] == second_id for item in second_items)


def test_responses_preserves_modern_opaque_input_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    opaque_items = [
        {"id": "sh_1", "type": "shell_call", "status": "completed", "action": {"command": "pwd"}},
        {"id": "sho_1", "type": "shell_call_output", "call_id": "sh_1", "output": "ok"},
        {"id": "ts_1", "type": "tool_search_call", "status": "completed", "query": "aegis"},
        {"id": "tso_1", "type": "tool_search_output", "call_id": "ts_1", "output": [{"title": "docs"}]},
        {"id": "at_1", "type": "additional_tools", "tools": [{"type": "web_search_preview"}]},
        {"id": "ap_1", "type": "apply_patch_call", "status": "completed", "input": "*** Begin Patch"},
        {"id": "apo_1", "type": "apply_patch_call_output", "call_id": "ap_1", "output": "patched"},
        {"id": "ct_1", "type": "compaction_trigger", "reason": "context_window"},
        {"id": "cmp_1", "type": "compaction", "summary": [{"type": "summary_text", "text": "kept state"}]},
        {"id": "ref_1", "type": "item_reference", "item_id": "msg_prior"},
    ]
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                *opaque_items,
                {"role": "user", "content": "continue"},
            ],
        })
        response_id = json.loads(data)["id"]
    finally:
        srv.shutdown()
        srv.server_close()

    srv2, port2 = _serve(server.make_handler(Config.load()))
    try:
        items_status, items_data = _request(port2, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
        second_status, second_data = _request(port2, "POST", "/v1/responses", {
            "previous_response_id": response_id,
            "input": "follow up",
        })
        second_id = json.loads(second_data)["id"]
        second_items_status, second_items_data = _request(
            port2,
            "GET",
            f"/v1/responses/{second_id}/input_items?order=asc",
        )
    finally:
        srv2.shutdown()
        srv2.server_close()

    assert status == 200
    assert _FakeRunner.calls[0]["prompt"].content == "continue"
    assert _FakeRunner.calls[0]["history"] == []
    assert items_status == 200
    items = json.loads(items_data)["data"]
    first_opaque = items[:len(opaque_items)]
    assert [item["type"] for item in first_opaque] == [item["type"] for item in opaque_items]
    assert items[0]["action"] == {"command": "pwd"}
    assert first_opaque[2]["query"] == "aegis"
    assert first_opaque[3]["output"] == [{"title": "docs"}]
    assert first_opaque[4]["tools"] == [{"type": "web_search_preview"}]
    assert first_opaque[7]["reason"] == "context_window"
    assert first_opaque[-2]["summary"] == [{"type": "summary_text", "text": "kept state"}]
    assert first_opaque[-1]["item_id"] == "msg_prior"
    assert all(item["response_id"] == response_id for item in items)
    assert second_status == 200
    assert second_items_status == 200
    assert _FakeRunner.calls[1]["prompt"].content == "follow up"
    assert [m.content for m in _FakeRunner.calls[1]["history"]] == ["continue", "hello"]
    second_items = json.loads(second_items_data)["data"]
    second_opaque = second_items[:len(opaque_items)]
    assert [item["type"] for item in second_opaque] == [item["type"] for item in opaque_items]
    assert second_items[0]["action"] == {"command": "pwd"}
    assert second_opaque[2]["query"] == "aegis"
    assert second_opaque[3]["output"] == [{"title": "docs"}]
    assert second_opaque[4]["tools"] == [{"type": "web_search_preview"}]
    assert second_opaque[7]["reason"] == "context_window"
    assert second_opaque[-2]["summary"] == [{"type": "summary_text", "text": "kept state"}]
    assert second_opaque[-1]["item_id"] == "msg_prior"
    assert all(item["response_id"] == second_id for item in second_items)


def test_responses_still_rejects_malformed_message_content_with_opaque_types(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "summary_text", "text": "not valid message content"}],
                },
            ],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "unsupported_content_part"
    assert error["param"] == "input[0].content[0].type"
    assert _FakeRunner.calls == []


def test_responses_accepts_function_call_output_input_item(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    function_output = {
        "type": "function_call_output",
        "call_id": "call_fetch",
        "output": "tool says hi",
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"role": "user", "content": "start"},
                function_output,
                {"role": "user", "content": "continue"},
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "continue"
    assert [(m.role, m.content) for m in call["history"]] == [
        ("user", "start"),
        ("tool", "tool says hi"),
    ]
    assert call["history"][1].tool_call_id == "call_fetch"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert [item["type"] for item in items] == [
        "message",
        "function_call_output",
        "message",
    ]
    assert items[1]["call_id"] == "call_fetch"
    assert items[1]["output"] == "tool says hi"


def test_responses_developer_after_user_is_not_active_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, _data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"role": "user", "content": "draft the note"},
                {"role": "developer", "content": "keep it terse"},
            ],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "draft the note"
    assert [m.content for m in call["history"]] == [
        "<developer_instructions>\nkeep it terse\n</developer_instructions>",
    ]


def test_responses_user_then_function_output_preserves_chronology(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    function_output = {
        "type": "function_call_output",
        "call_id": "call_fetch",
        "output": "tool says hi",
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"role": "user", "content": "start"},
                function_output,
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == ""
    assert call["prompt"].meta["_responses_synthetic_prompt"] is True
    assert [(m.role, m.content) for m in call["history"]] == [
        ("user", "start"),
        ("tool", "tool says hi"),
    ]
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert [item["type"] for item in items] == ["message", "function_call_output"]
    assert items[0]["content"][0]["text"] == "start"
    assert items[1]["call_id"] == "call_fetch"


def test_responses_accepts_function_call_input_item(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    function_call = {
        "type": "function_call",
        "call_id": "call_fetch",
        "name": "fetch",
        "arguments": {"url": "https://example.test"},
    }
    function_output = {
        "type": "function_call_output",
        "call_id": "call_fetch",
        "output": "tool says hi",
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                function_call,
                function_output,
                {"role": "user", "content": "continue"},
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert call["prompt"].content == "continue"
    assert len(call["history"]) == 2
    assert call["history"][0].role == "assistant"
    assert call["history"][0].tool_calls[0].id == "call_fetch"
    assert call["history"][0].tool_calls[0].name == "fetch"
    assert call["history"][0].tool_calls[0].arguments == {"url": "https://example.test"}
    assert call["history"][1].role == "tool"
    assert call["history"][1].tool_call_id == "call_fetch"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert [item["type"] for item in items] == [
        "function_call",
        "function_call_output",
        "message",
    ]
    assert items[0]["call_id"] == "call_fetch"
    assert items[0]["name"] == "fetch"
    assert json.loads(items[0]["arguments"]) == {"url": "https://example.test"}
    assert items[1]["call_id"] == "call_fetch"


def test_responses_rejects_invalid_function_call_input_item(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"type": "function_call", "call_id": "call_fetch", "arguments": {}},
                {"role": "user", "content": "continue"},
            ],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    body = json.loads(data)
    assert body["error"]["code"] == "invalid_function_call"
    assert body["error"]["param"] == "input[0].name"
    assert _FakeRunner.calls == []


def test_responses_function_call_output_preserves_multimodal_output_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    function_output = {
        "type": "function_call_output",
        "call_id": "call_vision",
        "output": [
            {"type": "text", "text": "Image loaded."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,XYZ"}},
        ],
    }
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {"role": "user", "content": "inspect"},
                function_output,
                {"role": "user", "content": "continue"},
            ],
        })
        response_id = json.loads(data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{response_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    call = _FakeRunner.calls[0]
    assert [(m.role, m.content) for m in call["history"]] == [
        ("user", "inspect"),
        ("tool", "Image loaded."),
    ]
    assert call["history"][1].tool_call_id == "call_vision"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[1]["type"] == "function_call_output"
    assert items[1]["call_id"] == "call_vision"
    assert items[1]["output"] == [
        {"type": "input_text", "text": "Image loaded."},
        {"type": "input_image", "image_url": "data:image/png;base64,XYZ"},
    ]


def test_responses_rejects_invalid_function_call_output_image(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": "call_vision",
                    "output": [
                        {"type": "input_image", "image_url": "data:text/plain;base64,SGVsbG8="},
                    ],
                },
                {"role": "user", "content": "continue"},
            ],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 400
    error = json.loads(data)["error"]
    assert error["code"] == "unsupported_content_type"
    assert error["param"] == "input[0].output[0].image_url"
    assert _FakeRunner.calls == []


def test_responses_function_call_output_continues_previous_response(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    output_parts = [{"type": "input_text", "text": "lookup result"}]
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "first",
        })
        first_id = json.loads(first_data)["id"]
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "previous_response_id": first_id,
            "input": [{
                "type": "function_call_output",
                "call_id": "call_lookup",
                "output": output_parts,
            }],
        })
        second_id = json.loads(second_data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{second_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == 200
    assert second_status == 200
    second_call = _FakeRunner.calls[1]
    assert second_call["prompt"].role == "user"
    assert second_call["prompt"].content == ""
    assert second_call["prompt"].meta["_responses_synthetic_prompt"] is True
    assert [(m.role, m.content) for m in second_call["history"]] == [
        ("user", "first"),
        ("assistant", "hello"),
        ("tool", "lookup result"),
    ]
    assert second_call["history"][2].tool_call_id == "call_lookup"
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[-1]["type"] == "function_call_output"
    assert items[-1]["call_id"] == "call_lookup"
    assert items[-1]["output"] == output_parts
    assert not any(
        item["type"] == "message"
        and item.get("role") == "user"
        and item.get("content") == [{"type": "input_text", "text": ""}]
        for item in items
    )


def test_responses_previous_response_replays_transcript_tool_calls_as_input_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.types import Message, ToolCall

    class ToolReplayRunner:
        calls = []

        def __init__(self, config, include_mcp=True):
            self.config = config
            self.include_mcp = include_mcp

        def run_prompt(self, prompt, **kwargs):
            self.calls.append({"prompt": prompt, **kwargs})
            session_id = kwargs.get("session_id") or "serve:tool-replay"
            if len(self.calls) == 1:
                messages = [
                    Message.user("search"),
                    Message.assistant(
                        "",
                        tool_calls=[
                            ToolCall(
                                id="call_search",
                                name="search",
                                arguments={"query": "aegis"},
                            ),
                        ],
                    ),
                    Message.tool("call_search", "search", "found docs"),
                    Message.assistant("done"),
                ]
                text = "done"
            else:
                messages = []
                text = "followed"
            return SimpleNamespace(
                text=text,
                session=SimpleNamespace(id=session_id, messages=messages),
                trace_id="trace_tool_replay",
                turn_id="turn_tool_replay",
                run_id="run_tool_replay",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model=kwargs.get("model") or "served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    ToolReplayRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", ToolReplayRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "search",
        })
        first_id = json.loads(first_data)["id"]
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "previous_response_id": first_id,
            "input": "continue",
        })
        second_id = json.loads(second_data)["id"]
        items_status, items_data = _request(port, "GET", f"/v1/responses/{second_id}/input_items?order=asc")
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == 200
    assert second_status == 200
    assert len(ToolReplayRunner.calls) == 2
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert [item["type"] for item in items] == [
        "message",
        "function_call",
        "function_call_output",
        "message",
        "message",
    ]
    function_call = next(item for item in items if item["type"] == "function_call")
    assert function_call["name"] == "search"
    assert function_call["call_id"] == "call_search"
    assert json.loads(function_call["arguments"]) == {"query": "aegis"}
    assert items[3]["role"] == "assistant"
    assert items[3]["content"] == [{"type": "output_text", "text": "done"}]
    assert items[4]["role"] == "user"
    assert items[4]["content"] == [{"type": "input_text", "text": "continue"}]
    assert any(
        item["type"] == "function_call_output"
        and item["call_id"] == "call_search"
        and item["output"] == [{"type": "input_text", "text": "found docs"}]
        for item in items
    )
    assert not any(
        item["type"] == "message"
        and item.get("role") == "assistant"
        and item.get("content") == [{"type": "output_text", "text": ""}]
        for item in items
    )


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


def test_responses_missing_previous_id_with_explicit_history_404s(monkeypatch, tmp_path):
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
            "conversation_history": [{"role": "user", "content": "first"}],
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 404
    assert "Previous response not found" in json.loads(data)["error"]
    assert _FakeRunner.calls == []


def test_responses_previous_response_can_continue_without_new_input(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "first",
        })
        first_id = json.loads(first_data)["id"]
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "previous_response_id": first_id,
        })
        second_id = json.loads(second_data)["id"]
        items_status, items_data = _request(
            port,
            "GET",
            f"/v1/responses/{second_id}/input_items?order=asc",
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert first_status == 200
    assert second_status == 200
    assert json.loads(second_data)["previous_response_id"] == first_id
    second_call = _FakeRunner.calls[1]
    assert second_call["prompt"].content == ""
    assert second_call["prompt"].meta["_responses_synthetic_prompt"] is True
    assert [m.content for m in second_call["history"]] == ["first", "hello"]
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert [item["type"] for item in items] == ["message", "message"]
    assert items[0]["content"] == [{"type": "input_text", "text": "first"}]
    assert items[1]["role"] == "assistant"


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


def test_conversations_api_tracks_latest_response_and_items(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _FakeRunner.calls = []
    monkeypatch.setattr(server, "SurfaceRunner", _FakeRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(port, "POST", "/v1/conversations", {
            "id": "thread-api",
            "metadata": {"topic": "deploy"},
        })
        list_status, list_data = _request(port, "GET", "/v1/conversations")
        empty_items_status, empty_items_data = _request(
            port,
            "GET",
            "/v1/conversations/thread-api/items?order=asc",
        )
        first_status, first_data = _request(port, "POST", "/v1/responses", {
            "input": "first",
            "conversation": "thread-api",
        })
        first_id = json.loads(first_data)["id"]
        get_status, get_data = _request(port, "GET", "/v1/conversations/thread-api")
        items_status, items_data = _request(
            port,
            "GET",
            "/v1/conversations/thread-api/items?order=asc",
        )
        second_status, second_data = _request(port, "POST", "/v1/responses", {
            "input": "second",
            "conversation": "thread-api",
        })
        delete_status, delete_data = _request(port, "DELETE", "/v1/conversations/thread-api")
        missing_status, missing_data = _request(port, "GET", "/v1/conversations/thread-api")
    finally:
        srv.shutdown()
        srv.server_close()

    created = json.loads(create_data)
    assert create_status == 201
    assert created["id"] == "thread-api"
    assert created["object"] == "conversation"
    assert created["metadata"] == {"topic": "deploy"}
    assert created["latest_response_id"] is None
    assert list_status == 200
    assert any(row["id"] == "thread-api" for row in json.loads(list_data)["data"])
    assert empty_items_status == 200
    assert json.loads(empty_items_data)["data"] == []

    assert first_status == 200
    retrieved = json.loads(get_data)
    assert get_status == 200
    assert retrieved["latest_response_id"] == first_id
    assert items_status == 200
    items = json.loads(items_data)["data"]
    assert items[0]["content"] == [{"type": "input_text", "text": "first"}]
    assert json.loads(items_data)["conversation_id"] == "thread-api"
    assert json.loads(items_data)["response_id"] == first_id

    assert second_status == 200
    second = json.loads(second_data)
    assert second["conversation"] == "thread-api"
    assert second["previous_response_id"] == first_id
    assert [m.content for m in _FakeRunner.calls[1]["history"][:2]] == ["first", "hello"]
    assert delete_status == 200
    assert json.loads(delete_data)["deleted"] is True
    assert missing_status == 404
    assert json.loads(missing_data)["id"] == "thread-api"


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
            "include": "output[*].content",
            "max_completion_tokens": 66,
            "reasoning": "max",
            "parallel_tool_calls": False,
            "metadata": {"session_id": "serve:responses-stream"},
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    events = _sse_events(data)
    names = [name for name, _payload in events]
    assert names[:5] == [
        "response.created",
        "response.in_progress",
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
    created = next(payload for name, payload in events if name == "response.created")
    in_progress = next(payload for name, payload in events if name == "response.in_progress")
    completed = next(payload for name, payload in events if name == "response.completed")
    assert created["response"]["parallel_tool_calls"] is False
    assert in_progress["response"]["id"] == created["response"]["id"]
    assert in_progress["response"]["status"] == "in_progress"
    assert created["response"]["include"] == ["output[*].content"]
    assert completed["response"]["parallel_tool_calls"] is False
    assert completed["response"]["include"] == ["output[*].content"]
    assert completed["response"]["metadata"]["reasoning_effort"] == "xhigh"
    assert _FakeRunner.calls[0]["stream"] is True
    assert _FakeRunner.calls[0]["meta"]["runtime_controls"]["reasoning_effort"] == "xhigh"
    assert _FakeRunner.calls[0]["max_tokens"] == 66


def test_responses_stream_idempotency_key_replays_completed_request(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class CountingStreamRunner:
        calls = 0

        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            type(self).calls += 1
            if kwargs.get("on_event"):
                kwargs["on_event"]({"type": "assistant_delta", "text": f"stream-{type(self).calls}"})
            session_id = kwargs.get("session_id") or "serve:stream-idem"
            return SimpleNamespace(
                text=f"stream-{type(self).calls}",
                session=SimpleNamespace(id=session_id),
                trace_id=f"trace_stream_idem_{type(self).calls}",
                turn_id=f"turn_stream_idem_{type(self).calls}",
                run_id=f"run_stream_idem_{type(self).calls}",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", CountingStreamRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        headers = {"Idempotency-Key": "idem-response-stream"}
        body = {
            "stream": True,
            "input": "same",
            "metadata": {"session_id": "serve:stream-idem"},
        }
        first_status, first_data = _request(port, "POST", "/v1/responses", body, headers=headers)
        second_status, second_data = _request(port, "POST", "/v1/responses", body, headers=headers)
        third_status, third_data = _request(port, "POST", "/v1/responses", {
            **body,
            "input": "different",
        }, headers=headers)
    finally:
        srv.shutdown()
        srv.server_close()

    first_completed = next(payload for name, payload in _sse_events(first_data) if name == "response.completed")
    second_events = _sse_events(second_data)
    second_completed = next(payload for name, payload in second_events if name == "response.completed")
    third_completed = next(payload for name, payload in _sse_events(third_data) if name == "response.completed")
    assert first_status == second_status == third_status == 200
    assert second_completed["response"]["id"] == first_completed["response"]["id"]
    assert second_completed["response"]["output_text"] == "stream-1"
    assert third_completed["response"]["id"] != first_completed["response"]["id"]
    assert third_completed["response"]["output_text"] == "stream-2"
    assert [name for name, _payload in second_events][-2:] == ["response.completed", "done"]
    assert CountingStreamRunner.calls == 2


def test_responses_stream_idempotency_key_single_flights_concurrent_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class SlowStreamRunner:
        calls = 0
        entered = threading.Event()
        release = threading.Event()

        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            type(self).calls += 1
            if kwargs.get("on_event"):
                kwargs["on_event"]({"type": "assistant_delta", "text": "slow-stream"})
            type(self).entered.set()
            assert type(self).release.wait(5)
            session_id = kwargs.get("session_id") or "serve:stream-singleflight"
            return SimpleNamespace(
                text="slow-stream",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_stream_singleflight",
                turn_id="turn_stream_singleflight",
                run_id="run_stream_singleflight",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", SlowStreamRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    results: list[tuple[int, str]] = []
    try:
        headers = {"Idempotency-Key": "idem-response-stream-flight"}
        body = {
            "stream": True,
            "input": "same",
            "metadata": {"session_id": "serve:stream-singleflight"},
        }
        threads = [
            threading.Thread(target=lambda: results.append(
                _request(port, "POST", "/v1/responses", body, headers=headers)
            ))
            for _ in range(2)
        ]
        threads[0].start()
        assert SlowStreamRunner.entered.wait(5)
        threads[1].start()
        time.sleep(0.2)
        assert SlowStreamRunner.calls == 1
    finally:
        SlowStreamRunner.release.set()
        for thread in locals().get("threads", []):
            thread.join(timeout=5)
        srv.shutdown()
        srv.server_close()

    assert len(results) == 2
    assert all(status == 200 for status, _data in results)
    completed = [
        next(payload for name, payload in _sse_events(data) if name == "response.completed")
        for _status, data in results
    ]
    assert completed[0]["response"]["id"] == completed[1]["response"]["id"]
    assert completed[0]["response"]["output_text"] == "slow-stream"
    assert completed[1]["response"]["output_text"] == "slow-stream"
    assert SlowStreamRunner.calls == 1


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


def test_responses_stream_store_false_cancel_does_not_persist(monkeypatch, tmp_path):
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
                "store": False,
                "input": "wait for cancel",
                "conversation": "volatile-thread",
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        event, payload = _read_sse_event(resp)
        assert event == "response.created"
        response_id = payload["response"]["id"]
        assert payload["response"]["store"] is False
        assert _BlockingResponsesRunner.started.wait(1)

        cancel_status, cancel_data = _request(port, "POST", f"/v1/responses/{response_id}/cancel", {})
        _BlockingResponsesRunner.release.set()
        tail = resp.read().decode()
        get_status, _get_data = _request(port, "GET", f"/v1/responses/{response_id}")
        next_status, next_data = _request(port, "POST", "/v1/responses", {
            "input": "next",
            "conversation": "volatile-thread",
        })
    finally:
        _BlockingResponsesRunner.release.set()
        conn.close()
        srv.shutdown()
        srv.server_close()

    assert cancel_status == 200
    assert json.loads(cancel_data)["status"] == "cancelled"
    assert _BlockingResponsesRunner.agents
    assert _BlockingResponsesRunner.agents[0].cancel_event.is_set()
    assert "response.cancelled" in [name for name, _payload in _sse_events(tail)]
    assert get_status == 404
    assert next_status == 200
    assert json.loads(next_data)["previous_response_id"] is None


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
                    assert await asyncio.to_thread(_BlockingResponsesRunner.started.wait, 1)
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


def test_responses_disconnect_drops_late_stream_result(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _LateAccessStreamingRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _LateAccessStreamingRunner)

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
                        "metadata": {"session_id": "serve:response-late-drop"},
                    },
                ) as resp:
                    assert resp.status == 200
                    event_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert event_line == b"event: response.created\n"
                    assert data_line.startswith(b"data: ")
                    assert await asyncio.to_thread(_LateAccessStreamingRunner.started.wait, 1)
                    resp.close()
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if (
                            _LateAccessStreamingRunner.agents
                            and _LateAccessStreamingRunner.agents[0].cancel_event.is_set()
                        ):
                            break
                        await asyncio.sleep(0.05)
                    _LateAccessStreamingRunner.release.set()
                    return True
        finally:
            _LateAccessStreamingRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True
    assert not _LateAccessResult.accessed.is_set()


def test_responses_nonstream_disconnect_cancels_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _LateAccessStreamingRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _LateAccessStreamingRunner)

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
                request_task = asyncio.ensure_future(session.post(
                    f"http://127.0.0.1:{port}/v1/responses",
                    json={
                        "input": "disconnect me",
                        "metadata": {"session_id": "serve:response-nonstream-disconnect"},
                    },
                ))
                assert await asyncio.to_thread(_LateAccessStreamingRunner.started.wait, 1)
                request_task.cancel()
                try:
                    response = await request_task
                except (asyncio.CancelledError, Exception):
                    response = None
                if response is not None:
                    response.close()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if (
                        _LateAccessStreamingRunner.agents
                        and _LateAccessStreamingRunner.agents[0].cancel_event.is_set()
                    ):
                        return True
                    await asyncio.sleep(0.05)
                return False
        finally:
            _LateAccessStreamingRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True
    assert not _LateAccessResult.accessed.is_set()


def test_responses_nonstream_active_cancel_returns_cancelled_response(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingResponsesRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingResponsesRunner)
    original_new_id = server.new_id

    def fake_new_id(prefix: str):
        if prefix == "resp":
            return "resp_active_cancel"
        return original_new_id(prefix)

    monkeypatch.setattr(server, "new_id", fake_new_id)
    srv, port = _serve(server.make_handler(Config.load()))
    post_result = {}

    def post_response():
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "cancel while running",
            "metadata": {"session_id": "serve:response-active-cancel"},
        })
        post_result.update({"status": status, "data": data})

    thread = threading.Thread(target=post_response, daemon=True)
    try:
        thread.start()
        assert _BlockingResponsesRunner.started.wait(2)
        cancel_status, cancel_data = _request(
            port,
            "POST",
            "/v1/responses/resp_active_cancel/cancel",
            {},
        )
        mid_get_status, mid_get_data = _request(port, "GET", "/v1/responses/resp_active_cancel")
        _BlockingResponsesRunner.release.set()
        thread.join(5)
        final_get_status, final_get_data = _request(port, "GET", "/v1/responses/resp_active_cancel")
    finally:
        _BlockingResponsesRunner.release.set()
        srv.shutdown()
        srv.server_close()

    assert cancel_status == 200
    cancelled = json.loads(cancel_data)
    assert cancelled["id"] == "resp_active_cancel"
    assert cancelled["status"] == "cancelled"
    assert cancelled["metadata"]["cancel_reason"] == "API cancel requested"
    assert _BlockingResponsesRunner.agents
    assert _BlockingResponsesRunner.agents[0].cancel_event.is_set()
    assert mid_get_status == 200
    assert json.loads(mid_get_data)["status"] == "cancelled"
    assert post_result["status"] == 200
    assert json.loads(post_result["data"])["status"] == "cancelled"
    assert final_get_status == 200
    assert json.loads(final_get_data)["status"] == "cancelled"


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
    assert function_call["id"].startswith("fc_")
    assert function_call["status"] == "in_progress"
    assert json.loads(function_call["arguments"]) == {"query": "aegis"}
    assert function_output["call_id"] == "call_search"
    assert function_output["id"].startswith("fco_")
    assert function_output["status"] == "completed"
    assert function_output["output"][0]["text"] == "found docs"
    assert any(item["type"] == "function_call" and item["status"] == "completed" for item in done)
    assert any(item["type"] == "function_call_output" for item in done)
    assert [item["type"] for item in completed["response"]["output"]] == [
        "function_call",
        "function_call_output",
        "message",
    ]
    assert [item["status"] for item in completed["response"]["output"]] == [
        "completed",
        "completed",
        "completed",
    ]


def test_responses_stream_tool_error_status_survives_completed_response(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class ToolErrorStreamRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            kwargs["on_event"]({
                "type": "tool_start",
                "id": "call_lookup",
                "name": "lookup",
                "args": {"id": 1},
            })
            kwargs["on_event"]({
                "type": "tool_result",
                "id": "call_lookup",
                "name": "lookup",
                "is_error": True,
                "data": {"error": "not found"},
            })
            kwargs["on_event"]({"type": "assistant_delta", "text": "handled"})
            session_id = kwargs.get("session_id") or "serve:tool-error-stream"
            return SimpleNamespace(
                text="handled",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_tool_error_stream",
                turn_id="turn_tool_error_stream",
                run_id="run_tool_error_stream",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", ToolErrorStreamRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "stream": True,
            "input": "lookup",
            "metadata": {"session_id": "serve:tool-error-stream"},
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    events = _sse_events(data)
    done_items = [payload["item"] for name, payload in events if name == "response.output_item.done"]
    stream_output = next(item for item in done_items if item["type"] == "function_call_output")
    completed = next(payload for name, payload in events if name == "response.completed")
    final_output = completed["response"]["output"]

    assert stream_output["status"] == "failed"
    assert stream_output["output"][0]["text"] == '{"error": "not found"}'
    assert final_output[0]["type"] == "function_call"
    assert final_output[0]["status"] == "completed"
    assert final_output[1]["type"] == "function_call_output"
    assert final_output[1]["status"] == "failed"
    assert final_output[1]["output"][0]["text"] == '{"error": "not found"}'


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
    assert response["output"][0]["id"].startswith("fc_")
    assert response["output"][0]["status"] == "completed"
    assert json.loads(response["output"][0]["arguments"]) == {"query": "aegis"}
    assert response["output"][1]["call_id"] == "call_search"
    assert response["output"][1]["id"].startswith("fco_")
    assert response["output"][1]["status"] == "completed"
    assert response["output"][1]["output"][0]["text"] == "found docs"
    assert response["output"][2]["id"].startswith("msg_")
    assert response["output"][2]["status"] == "completed"
    assert response["output_text"] == "done"
    assert get_status == 200
    stored = json.loads(get_data)
    assert [item["type"] for item in stored["output"]] == [
        "function_call",
        "function_call_output",
        "message",
    ]


def test_responses_nonstream_tool_error_output_preserves_failed_status_and_json_data(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    class ToolErrorBatchRunner:
        def __init__(self, config, include_mcp=True):
            pass

        def run_prompt(self, prompt, **kwargs):
            session_id = kwargs.get("session_id") or "serve:tool-error-batch"
            return SimpleNamespace(
                text="handled",
                session=SimpleNamespace(id=session_id),
                trace_id="trace_tool_error_batch",
                turn_id="turn_tool_error_batch",
                run_id="run_tool_error_batch",
                events=[
                    {
                        "type": "tool_start",
                        "id": "call_lookup",
                        "name": "lookup",
                        "args": {"id": 1},
                    },
                    {
                        "type": "tool_result",
                        "id": "call_lookup",
                        "name": "lookup",
                        "is_error": True,
                        "data": {"error": "not found"},
                    },
                ],
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    monkeypatch.setattr(server, "SurfaceRunner", ToolErrorBatchRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(port, "POST", "/v1/responses", {
            "input": "lookup",
            "metadata": {"session_id": "serve:tool-error-batch"},
        })
        response = json.loads(data)
        get_status, get_data = _request(port, "GET", f"/v1/responses/{response['id']}")
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert response["output"][0]["type"] == "function_call"
    assert response["output"][0]["status"] == "completed"
    assert response["output"][1]["type"] == "function_call_output"
    assert response["output"][1]["status"] == "failed"
    assert response["output"][1]["output"][0]["text"] == '{"error": "not found"}'
    assert get_status == 200
    stored = json.loads(get_data)
    assert stored["output"][1]["status"] == "failed"
    assert stored["output"][1]["output"][0]["text"] == '{"error": "not found"}'


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
        fourth_status, fourth_data = _request(port, "POST", "/v1/responses", {
            "input": "same",
            "metadata": {"session_id": "serve:idem-other"},
        }, headers=headers)
    finally:
        srv.shutdown()
        srv.server_close()

    first = json.loads(first_data)
    second = json.loads(second_data)
    third = json.loads(third_data)
    fourth = json.loads(fourth_data)
    assert first_status == second_status == third_status == fourth_status == 200
    assert second["id"] == first["id"]
    assert second["output_text"] == "reply-1"
    assert third["id"] != first["id"]
    assert third["output_text"] == "reply-2"
    assert fourth["id"] != first["id"]
    assert fourth["output_text"] == "reply-3"
    assert CountingRunner.calls == 3


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
        fourth_status, fourth_data = _request(
            port,
            "POST",
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "same"}], "metadata": {"session_id": "serve:chat-other"}},
            headers=headers,
        )
    finally:
        srv.shutdown()
        srv.server_close()

    first = json.loads(first_data)
    second = json.loads(second_data)
    third = json.loads(third_data)
    fourth = json.loads(fourth_data)
    assert first_status == second_status == third_status == fourth_status == 200
    assert second["id"] == first["id"]
    assert second["choices"][0]["message"]["content"] == "chat-1"
    assert third["id"] != first["id"]
    assert third["choices"][0]["message"]["content"] == "chat-2"
    assert fourth["id"] != first["id"]
    assert fourth["choices"][0]["message"]["content"] == "chat-3"
    assert CountingRunner.calls == 3


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
            {"prompt": "reply", "max_tokens": 44, "reasoning_effort": "minimal"},
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
    assert _FakeRunner.calls[0]["meta"]["runtime_controls"]["reasoning_effort"] == "minimal"
    assert _FakeRunner.calls[0]["max_tokens"] == 44
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
                "reasoning_effort": "high",
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
            {"id": "api-child", "title": "Explicit Fork", "reasoning": {"effort": "low"}},
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
    assert created["meta"]["runtime_controls"]["reasoning_effort"] == "high"
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
    assert forked["meta"]["runtime_controls"]["reasoning_effort"] == "low"
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


def test_session_chat_runtime_controls_reach_precreated_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    class RuntimeCaptureRunner:
        seen_controls = []

        def __init__(self, config, include_mcp=True):
            self.config = config
            self.include_mcp = include_mcp

        def make_agent(self, session, **_kwargs):
            type(self).seen_controls.append(dict(session.meta.get("runtime_controls") or {}))
            return SimpleNamespace(cancel_event=threading.Event(), tool_context=SimpleNamespace())

        def run_prompt(self, prompt, **kwargs):
            session = kwargs["session"]
            return SimpleNamespace(
                text="ok",
                message=Message.assistant("ok"),
                session=session,
                trace_id="trace_runtime",
                turn_id="turn_runtime",
                run_id="run_runtime",
                agent=SimpleNamespace(
                    provider=SimpleNamespace(model="served-model"),
                    budget=SimpleNamespace(usage=_Usage()),
                ),
            )

    RuntimeCaptureRunner.seen_controls = []
    monkeypatch.setattr(server, "SurfaceRunner", RuntimeCaptureRunner)
    session_id = "serve:runtime-controls"
    stored = Session(id=session_id, title="runtime session")
    stored.meta["runtime_controls"] = {"model": "sticky-model"}
    SessionStore().save(stored)

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        status, data = _request(
            port,
            "POST",
            f"/api/sessions/{session_id}/chat",
            {
                "prompt": "reply",
                "service_tier": "priority",
                "reasoning": {"effort": "xhigh"},
            },
        )
    finally:
        srv.shutdown()
        srv.server_close()

    assert status == 200
    assert json.loads(data)["text"] == "ok"
    assert RuntimeCaptureRunner.seen_controls == [{
        "model": "sticky-model",
        "service_tier": "priority",
        "reasoning_effort": "xhigh",
    }]


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
            {"prompt": "reply", "max_completion_tokens": 55, "reasoning": {"effort": "high"}},
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
    assert _FakeRunner.calls[0]["meta"]["runtime_controls"]["reasoning_effort"] == "high"
    assert _FakeRunner.calls[0]["max_tokens"] == 55


def test_session_chat_aiohttp_stream_disconnect_cancels_live_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.session import Session, SessionStore

    _BlockingResponsesRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingResponsesRunner)
    session_id = "serve:session-disconnect"
    SessionStore().save(Session(id=session_id, title="disconnect session"))

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
            async with ClientSession() as client:
                async with client.post(
                    f"http://127.0.0.1:{port}/api/sessions/{session_id}/chat/stream",
                    json={"prompt": "disconnect me"},
                ) as resp:
                    assert resp.status == 200
                    event_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert event_line == b"event: run.started\n"
                    assert data_line.startswith(b"data: ")
                    assert await asyncio.to_thread(_BlockingResponsesRunner.started.wait, 1)
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


def test_session_chat_disconnect_drops_late_stream_result(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.session import Session, SessionStore

    _LateAccessStreamingRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _LateAccessStreamingRunner)
    session_id = "serve:session-late-drop"
    SessionStore().save(Session(id=session_id, title="late drop session"))

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
            async with ClientSession() as client:
                async with client.post(
                    f"http://127.0.0.1:{port}/api/sessions/{session_id}/chat/stream",
                    json={"prompt": "disconnect me"},
                ) as resp:
                    assert resp.status == 200
                    event_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    data_line = await asyncio.wait_for(resp.content.readline(), timeout=1)
                    assert event_line == b"event: run.started\n"
                    assert data_line.startswith(b"data: ")
                    assert await asyncio.to_thread(_LateAccessStreamingRunner.started.wait, 1)
                    resp.close()
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if (
                            _LateAccessStreamingRunner.agents
                            and _LateAccessStreamingRunner.agents[0].cancel_event.is_set()
                        ):
                            break
                        await asyncio.sleep(0.05)
                    _LateAccessStreamingRunner.release.set()
                    return True
        finally:
            _LateAccessStreamingRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True
    assert not _LateAccessResult.accessed.is_set()


def test_session_chat_nonstream_disconnect_cancels_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config
    from aegis.session import Session, SessionStore

    _LateAccessStreamingRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _LateAccessStreamingRunner)
    session_id = "serve:session-nonstream-disconnect"
    SessionStore().save(Session(id=session_id, title="nonstream disconnect session"))

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
            async with ClientSession() as client:
                request_task = asyncio.ensure_future(client.post(
                    f"http://127.0.0.1:{port}/api/sessions/{session_id}/chat",
                    json={"prompt": "disconnect me"},
                ))
                assert await asyncio.to_thread(_LateAccessStreamingRunner.started.wait, 1)
                request_task.cancel()
                try:
                    response = await request_task
                except (asyncio.CancelledError, Exception):
                    response = None
                if response is not None:
                    response.close()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if (
                        _LateAccessStreamingRunner.agents
                        and _LateAccessStreamingRunner.agents[0].cancel_event.is_set()
                    ):
                        return True
                    await asyncio.sleep(0.05)
                return False
        finally:
            _LateAccessStreamingRunner.release.set()
            await runner.cleanup()

    assert asyncio.run(exercise()) is True
    assert not _LateAccessResult.accessed.is_set()


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


class _QueuedStopRunRunner(_BlockingRunRunner):
    make_agent_started = threading.Event()
    allow_make_agent = threading.Event()
    run_prompt_called = threading.Event()

    @classmethod
    def reset(cls):
        super().reset()
        cls.make_agent_started = threading.Event()
        cls.allow_make_agent = threading.Event()
        cls.run_prompt_called = threading.Event()

    def make_agent(self, **kwargs):
        self.make_agent_started.set()
        self.allow_make_agent.wait(5)
        return super().make_agent(**kwargs)

    def run_prompt(self, prompt, **kwargs):
        self.run_prompt_called.set()
        return super().run_prompt(prompt, **kwargs)


class _EmittingRunRunner(_BlockingRunRunner):
    @classmethod
    def reset(cls):
        cls.started = threading.Event()
        cls.release = threading.Event()
        cls.agents = []
        cls.calls = []

    def run_prompt(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("on_event"):
            kwargs["on_event"]({"type": "assistant_delta", "text": "hel"})
            kwargs["on_event"]({"type": "tool_start", "name": "read_file", "summary": "reading"})
            kwargs["on_event"]({"type": "assistant_delta", "text": "lo"})
        self.started.set()
        session = kwargs.get("session") or SimpleNamespace(id="serve:event-run")
        return SimpleNamespace(
            text="hello",
            session=session,
            trace_id="trace_events",
            turn_id="turn_events",
            run_id="surface_events",
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


class _RepeatedApprovalRunRunner:
    completed = threading.Event()
    calls = []

    def __init__(self, config, include_mcp=True):
        self.config = config
        self.include_mcp = include_mcp

    @classmethod
    def reset(cls):
        cls.completed = threading.Event()
        cls.calls = []

    def load_or_create_session(self, session_id=None, title=None, surface="", meta=None):
        return SimpleNamespace(id=session_id or "serve:approval-reuse-run", title=title or "", meta=meta or {})

    def make_agent(self, **kwargs):
        agent = SimpleNamespace(cancel_event=threading.Event(), approver=kwargs.get("approver"))

        def cancel():
            agent.cancel_event.set()

        agent.cancel = cancel
        return agent

    def run_prompt(self, prompt, **kwargs):
        agent = kwargs["agent"]
        first = agent.approver("Allow shell command?")
        second = agent.approver("Allow shell command?")
        self.calls.append({"prompt": prompt, "first": first, "second": second, **kwargs})
        self.completed.set()
        session = kwargs.get("session") or SimpleNamespace(id="serve:approval-reuse-run")
        return SimpleNamespace(
            text=f"first={first} second={second}",
            session=session,
            trace_id="trace_approval_reuse",
            turn_id="turn_approval_reuse",
            run_id="surface_approval_reuse",
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


def test_server_run_stop_before_prompt_does_not_execute_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    _QueuedStopRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _QueuedStopRunRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "queued run",
            "session_id": "serve:queued-stop",
        })
        run_id = json.loads(create_data)["run_id"]
        assert _QueuedStopRunRunner.make_agent_started.wait(2)
        stop_status, stop_data = _request(port, "POST", f"/v1/runs/{run_id}/stop", {})
        assert not _QueuedStopRunRunner.run_prompt_called.is_set()
        _QueuedStopRunRunner.allow_make_agent.set()

        final = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
            assert get_status == 200
            final = json.loads(get_data)
            if final.get("run", {}).get("status") == "cancelled":
                break
            time.sleep(0.05)
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
    finally:
        _QueuedStopRunRunner.allow_make_agent.set()
        srv.shutdown()
        srv.server_close()

    assert create_status == 202
    assert stop_status == 200
    assert json.loads(stop_data)["status"] == "stopping"
    assert _QueuedStopRunRunner.agents
    assert _QueuedStopRunRunner.agents[0].cancel_event.is_set()
    assert _QueuedStopRunRunner.calls == []
    assert not _QueuedStopRunRunner.run_prompt_called.is_set()
    assert final["run"]["status"] == "cancelled"
    assert final["run"]["result"] == ""
    assert events_status == 200
    assert any(event["type"] == "run.cancelled" for event in json.loads(events_data)["events"])


def test_server_run_events_disconnect_does_not_cancel_run(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _BlockingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _BlockingRunRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    stream_conn = None
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "slow event stream",
            "session_id": "serve:events-disconnect",
        })
        create_body = json.loads(create_data)
        run_id = create_body["run_id"]
        assert _BlockingRunRunner.started.wait(2)

        stream_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        stream_conn.request("GET", f"/v1/runs/{run_id}/events", headers={"Accept": "text/event-stream"})
        resp = stream_conn.getresponse()
        first_event = _read_sse_event(resp)
        stream_conn.close()
        stream_conn = None

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
        if stream_conn is not None:
            stream_conn.close()
        _BlockingRunRunner.release.set()
        srv.shutdown()
        srv.server_close()

    assert create_status == 202
    assert first_event[0] == "event"
    assert first_event[1]["type"] == "run.queued"
    assert _BlockingRunRunner.agents
    assert _BlockingRunRunner.agents[0].cancel_event.is_set() is False
    assert final["run"]["status"] == "completed"
    assert final["run"]["output"] == "finished"


def test_server_run_events_normalize_agent_emitted_events(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    _EmittingRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _EmittingRunRunner)
    srv, port = _serve(server.make_handler(Config.load()))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "emit events",
            "session_id": "serve:events-normalized",
        })
        run_id = json.loads(create_data)["run_id"]
        assert _EmittingRunRunner.started.wait(2)

        events_status = 0
        events_payload = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
            events_payload = json.loads(events_data)
            types = [event.get("type") for event in events_payload.get("events", [])]
            if "run.completed" in types:
                break
            time.sleep(0.05)
    finally:
        srv.shutdown()
        srv.server_close()

    assert create_status == 202
    assert events_status == 200
    events = events_payload["events"]
    event_types = [event.get("type") for event in events]
    assert "assistant_delta" in event_types
    assert "tool_start" in event_types
    assert "run.completed" in event_types
    assert [event["sequence_number"] for event in events] == list(range(len(events)))
    for event in events:
        assert event["object"] == "hermes.run.event"
        assert event["run_id"] == run_id
        assert isinstance(event["id"], str) and event["id"].startswith("evt_")
    tool_event = next(event for event in events if event["type"] == "tool_start")
    assert tool_event["name"] == "read_file"
    assert tool_event["metadata"]["type"] == "tool_start"
    delta_event = next(event for event in events if event["type"] == "assistant_delta")
    assert delta_event["text"] == "hel"


def test_server_run_approval_unknown_run_returns_404(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        get_status, get_data = _request(port, "GET", "/v1/runs/run_missing/approval")
        post_status, post_data = _request(
            port,
            "POST",
            "/v1/runs/run_missing/approval",
            {"choice": "once"},
        )
    finally:
        srv.shutdown()
        srv.server_close()

    get_body = json.loads(get_data)
    post_body = json.loads(post_data)
    assert get_status == 404
    assert post_status == 404
    assert get_body["run_id"] == "run_missing"
    assert post_body["run_id"] == "run_missing"
    assert get_body["error"]["code"] == "run_not_found"
    assert post_body["error"]["code"] == "run_not_found"


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
        stop_status, stop_data = _request(port, "POST", f"/v1/runs/{run_id}/stop", {})
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
    stop_body = json.loads(stop_data)
    assert get_status == 200
    assert run["id"] == run_id
    assert run["run_id"] == run_id
    assert run["object"] == "hermes.run"
    assert run["status"] == "completed"
    assert run["session_id"] == "serve:persist-run"
    assert run["result"] == "finished"
    assert stop_status == 409
    assert stop_body["error"]["code"] == "run_not_active"
    assert stop_body["run"]["id"] == run_id
    assert stop_body["run"]["status"] == "completed"
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
        duplicate_status, duplicate_data = _request(
            port,
            "POST",
            f"/v1/runs/{run_id}/approval",
            {"approval_id": pending["pending"][0]["id"], "choice": "deny"},
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
    duplicate = json.loads(duplicate_data)
    assert duplicate_status == 409
    assert duplicate["error"]["code"] == "approval_not_pending"
    assert _ApprovalBlockingRunRunner.approval_returned.wait(0.1)
    assert _ApprovalBlockingRunRunner.calls[0]["approved"] is True
    assert final["run"]["status"] == "completed"
    assert final["output"] == "approved=True"
    assert events_status == 200
    assert "approval.request" in event_types
    assert event_types.count("approval.responded") == 1
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


def test_server_run_approval_session_choice_reuses_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.server as server
    from aegis.config import Config

    _RepeatedApprovalRunRunner.reset()
    monkeypatch.setattr(server, "SurfaceRunner", _RepeatedApprovalRunRunner)
    cfg = Config.load()
    cfg.data.setdefault("server", {})["approval_timeout_seconds"] = 60
    srv, port = _serve(server.make_handler(cfg))
    try:
        create_status, create_data = _request(port, "POST", "/v1/runs", {
            "input": "needs repeated approval",
            "session_id": "serve:approval-reuse-run",
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
            {"choice": "session", "resolve_all": True},
        )
        assert _RepeatedApprovalRunRunner.completed.wait(2)

        final = {}
        deadline = time.time() + 2
        while time.time() < deadline:
            get_status, get_data = _request(port, "GET", f"/v1/runs/{run_id}")
            final = json.loads(get_data)
            if final.get("run", {}).get("status") == "completed":
                break
            time.sleep(0.05)
        pending_after_status, pending_after_data = _request(port, "GET", f"/v1/runs/{run_id}/approval")
        events_status, events_data = _request(port, "GET", f"/v1/runs/{run_id}/events")
    finally:
        srv.shutdown()
        srv.server_close()

    approval = json.loads(approval_data)
    pending_after = json.loads(pending_after_data)
    events = json.loads(events_data)["events"]
    event_types = [event.get("type") for event in events]
    assert create_status == 202
    assert pending_status == 200
    assert pending["pending"]
    assert approval_status == 200
    assert approval["choice"] == "session"
    assert _RepeatedApprovalRunRunner.calls[0]["first"] == "always"
    assert _RepeatedApprovalRunRunner.calls[0]["second"] == "always"
    assert final["run"]["status"] == "completed"
    assert final["output"] == "first=always second=always"
    assert pending_after_status == 200
    assert pending_after["pending"] == []
    assert events_status == 200
    assert event_types.count("approval.request") == 1
    assert "approval.reused" in event_types


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
        page_status, page_data = _request(port, "GET", "/api/jobs?limit=1&offset=0")
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
    assert listed["count"] == 1
    assert listed["summary"]["count"] == 1
    assert listed["summary"]["states"]["idle"] == 1
    assert page_status == 200
    page = json.loads(page_data)
    assert page["count"] == 1
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert page["has_more"] is False
    assert page["data"][0]["id"] == job_id
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


def test_server_api_jobs_validation_errors_are_400(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    srv, port = _serve(server.make_handler(Config.load()))
    try:
        invalid_workdir_status, invalid_workdir_data = _request(port, "POST", "/api/jobs", {
            "schedule": "every 1h",
            "prompt": "check status",
            "workdir": "relative/path",
        })
        invalid_max_status, invalid_max_data = _request(port, "POST", "/api/jobs", {
            "schedule": "every 1h",
            "prompt": "check status",
            "max_runs": "many",
        })
        create_status, create_data = _request(port, "POST", "/api/jobs", {
            "schedule": "every 1h",
            "prompt": "check status",
            "workdir": str(tmp_path),
        })
        job_id = json.loads(create_data)["id"]
        patch_workdir_status, patch_workdir_data = _request(port, "PATCH", f"/api/jobs/{job_id}", {
            "workdir": str(tmp_path / "missing"),
        })
        patch_max_status, patch_max_data = _request(port, "PATCH", f"/api/jobs/{job_id}", {
            "max_runs": "many",
        })
    finally:
        srv.shutdown()
        srv.server_close()

    assert invalid_workdir_status == 400
    assert "workdir" in json.loads(invalid_workdir_data)["error"]
    assert invalid_max_status == 400
    assert json.loads(invalid_max_data)["ok"] is False
    assert create_status == 201
    assert patch_workdir_status == 400
    assert "workdir" in json.loads(patch_workdir_data)["error"]
    assert patch_max_status == 400
    assert json.loads(patch_max_data)["id"] == job_id


def test_server_api_jobs_rejects_invalid_job_ids(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.server as server
    from aegis.config import Config

    caplog.set_level(logging.WARNING, logger="aegis.server")
    srv, port = _serve(server.make_handler(Config.load()))
    headers = {"X-Forwarded-For": "203.0.113.7", "User-Agent": "aegis-test"}
    try:
        calls = [
            ("GET", "/api/jobs/not-a-valid-hex!", None),
            ("PATCH", "/api/jobs/not-a-valid-hex!", {}),
            ("DELETE", "/api/jobs/not-a-valid-hex!", None),
            ("POST", "/api/jobs/not-a-valid-hex!/pause", {}),
            ("POST", "/api/jobs/not-a-valid-hex!/trigger", {}),
            ("GET", "/api/jobs/..%2F..%2Fetc%2Fpasswd", None),
        ]
        results = [
            _request(port, method, path, body, headers=headers)
            for method, path, body in calls
        ]
    finally:
        srv.shutdown()
        srv.server_close()

    for status, data in results:
        payload = json.loads(data)
        assert status == 400
        assert payload["ok"] is False
        assert payload["code"] == "invalid_job_id"
        assert "Invalid" in payload["error"]
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "Cron jobs API rejected invalid job_id" in logs
    assert "203.0.113.7" in logs
    assert "aegis-test" in logs
