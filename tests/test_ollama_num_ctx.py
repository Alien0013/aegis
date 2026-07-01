from __future__ import annotations

import copy


def _config(*, context_length: int | None = None, ollama_num_ctx: int | None = None):
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["model"].update(
        {
            "provider": "ollama",
            "default": "llama3.1:8b",
            "base_url": "http://localhost:11434/v1",
            "api_mode": "chat_completions",
        }
    )
    if context_length is not None:
        cfg.data["model"]["context_length"] = context_length
    if ollama_num_ctx is not None:
        cfg.data["model"]["ollama_num_ctx"] = ollama_num_ctx
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["skills"]["include_bundled"] = False
    cfg.data["agent"]["stream"] = False
    return cfg


def _patch_ollama_detection(monkeypatch, detector):
    from aegis import model_meta
    from aegis.providers import registry

    monkeypatch.setattr(model_meta, "query_ollama_num_ctx", detector)
    monkeypatch.setattr(registry, "query_ollama_num_ctx", detector, raising=False)


def _provider_num_ctx(provider) -> int:
    overrides = getattr(provider, "request_overrides", None)
    assert isinstance(overrides, dict)
    assert isinstance(overrides.get("extra_body"), dict)
    assert isinstance(overrides["extra_body"].get("options"), dict)
    return overrides["extra_body"]["options"]["num_ctx"]


def test_query_ollama_num_ctx_prefers_explicit_modelfile_num_ctx(monkeypatch):
    from aegis.model_meta import query_ollama_num_ctx

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "parameters": "temperature 0.7\nnum_ctx 32768\nstop <|eot_id|>",
                "model_info": {"llama.context_length": 131072},
            }

    class FakeClient:
        def __init__(self, timeout, headers):
            self.timeout = timeout
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, json):
            calls.append((url, json))
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = query_ollama_num_ctx("ollama/llama3.1:8b", "http://localhost:11434/v1")

    assert result == 32_768
    assert calls == [
        ("http://localhost:11434/api/show", {"name": "llama3.1:8b"}),
    ]


def test_query_ollama_num_ctx_falls_back_to_model_info_context_length(monkeypatch):
    from aegis.model_meta import query_ollama_num_ctx

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "parameters": "temperature 0.7",
                "model_info": {"qwen2.context_length": 65_536},
            }

    class FakeClient:
        def __init__(self, timeout, headers):
            self.timeout = timeout
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, json):
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    assert query_ollama_num_ctx("qwen2.5:7b", "http://localhost:11434") == 65_536


def test_agent_create_auto_detects_ollama_num_ctx_and_caps_to_config_context(
    monkeypatch,
    tmp_path,
):
    from aegis.agent.agent import Agent
    from aegis.providers import registry
    from aegis.session import Session

    detected = []

    def fake_query(model, base_url, api_key=""):
        detected.append((model, base_url, api_key))
        return 131_072

    monkeypatch.setattr(registry, "ensure_plugin_providers", lambda _config=None: None)
    _patch_ollama_detection(monkeypatch, fake_query)

    agent = Agent.create(
        _config(context_length=65_536),
        session=Session.create(),
        cwd=tmp_path,
    )

    assert detected == [("llama3.1:8b", "http://localhost:11434/v1", "")]
    assert agent.provider.context_length == 65_536
    assert _provider_num_ctx(agent.provider) == 65_536


def test_agent_create_explicit_ollama_num_ctx_wins_without_detection(monkeypatch, tmp_path):
    from aegis.agent.agent import Agent
    from aegis.providers import registry
    from aegis.session import Session

    def fail_query(*_args, **_kwargs):
        raise AssertionError("explicit model.ollama_num_ctx should skip detection")

    monkeypatch.setattr(registry, "ensure_plugin_providers", lambda _config=None: None)
    _patch_ollama_detection(monkeypatch, fail_query)

    agent = Agent.create(
        _config(context_length=65_536, ollama_num_ctx=131_072),
        session=Session.create(),
        cwd=tmp_path,
    )

    assert agent.provider.context_length == 65_536
    assert _provider_num_ctx(agent.provider) == 131_072


def test_chat_completions_transport_merges_request_overrides_extra_body(monkeypatch):
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.types import Message

    captured = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.chat_completions.httpx.Client", FakeClient)

    ChatCompletionsTransport().complete(
        base_url="http://localhost:11434/v1",
        auth=FakeAuth(),
        model="llama3.1:8b",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        request_overrides={"extra_body": {"options": {"num_ctx": 65_536}}},
    )

    assert captured["json"]["options"]["num_ctx"] == 65_536
    assert "extra_body" not in captured["json"]
