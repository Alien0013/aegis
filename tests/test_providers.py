"""Provider abstraction: wire conversion, auth, pools, fallback, reasoning, registry."""

from __future__ import annotations

import pytest


def test_chat_completions_wire_messages():
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.types import Message, ToolCall

    t = ChatCompletionsTransport()
    msgs = [
        Message.system("sys"),
        Message.user("hi"),
        Message.assistant("ok", [ToolCall("c1", "read_file", {"path": "a"})]),
        Message.tool("c1", "read_file", "contents"),
    ]
    wire = t._to_wire_messages(msgs)
    assert wire[0] == {"role": "system", "content": "sys"}
    assert wire[2]["tool_calls"][0]["id"] == "c1"
    assert wire[3] == {"role": "tool", "tool_call_id": "c1", "content": "contents"}


def test_chat_completions_tools_wire():
    from aegis.providers.chat_completions import ChatCompletionsTransport
    out = ChatCompletionsTransport()._to_wire_tools([{"name": "x", "description": "d", "parameters": {}}])
    assert out[0]["type"] == "function" and out[0]["function"]["name"] == "x"


def test_chat_completions_records_rate_and_balance_headers(monkeypatch):
    from aegis import ratelimit
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.types import Message

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        headers = {"x-account-balance": "12.34", "x-ratelimit-remaining-tokens": "900"}

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
            return FakeResponse()

    ratelimit._latest.clear()
    monkeypatch.setattr("aegis.providers.chat_completions.httpx.Client", FakeClient)

    ChatCompletionsTransport().complete(
        base_url="https://openrouter.ai/api/v1",
        auth=FakeAuth(),
        model="openrouter/test",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
    )

    assert ratelimit.balance()["provider"] == "openrouter"
    assert ratelimit.balance()["balance"] == "12.34"
    assert "tokens left (min): 900" in ratelimit.summary()


def test_chat_completions_includes_service_tier(monkeypatch):
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
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        service_tier="priority",
    )

    assert captured["json"]["service_tier"] == "priority"


def test_chat_completions_strips_service_tier_for_xai(monkeypatch):
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
        base_url="https://api.x.ai/v1",
        auth=FakeAuth(),
        model="grok-4.3",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        service_tier="priority",
    )

    assert "service_tier" not in captured["json"]


def test_chat_completions_stream_records_rate_and_balance_headers(monkeypatch):
    from aegis import ratelimit
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.types import Message

    captured = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200
        headers = {"x-ratelimit-remaining-credits": "8.50"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter([
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,'
                '"prompt_tokens_details":{"cached_tokens":2}}}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            captured["json"] = json
            return FakeStream()

    ratelimit._latest.clear()
    monkeypatch.setattr("aegis.providers.chat_completions.httpx.Client", FakeClient)

    resp = ChatCompletionsTransport().complete(
        base_url="https://openrouter.ai/api/v1",
        auth=FakeAuth(),
        model="openrouter/test",
        messages=[Message.user("hi")],
        tools=None,
        stream=True,
    )

    assert resp.text == "ok"
    assert captured["json"]["stream_options"] == {"include_usage": True}
    assert resp.usage.input_tokens == 9
    assert resp.usage.output_tokens == 4
    assert resp.usage.cache_read == 2
    assert ratelimit.balance()["provider"] == "openrouter"
    assert ratelimit.balance()["credits left"] == "8.50"


def test_tool_schema_sanitized_across_provider_transports():
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.providers.codex_app_server import CodexAppServerTransport
    from aegis.providers.responses import ResponsesTransport

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "path": {"type": ["string", "null"], "examples": ["README.md"]},
            "count": {"type": "integer", "exclusiveMinimum": 0},
        },
    }
    tool = {"name": "inspect", "description": "Inspect a thing", "parameters": schema}

    chat_params = ChatCompletionsTransport()._to_wire_tools([tool])[0]["function"]["parameters"]
    response_params = ResponsesTransport()._to_wire_tools([tool])[0]["parameters"]
    dynamic_params = CodexAppServerTransport()._to_dynamic_tools([tool])[0]["inputSchema"]

    for params in (chat_params, response_params, dynamic_params):
        assert "$schema" not in params
        assert params["properties"]["path"]["type"] == "string"
        assert "examples" not in params["properties"]["path"]
        assert "exclusiveMinimum" not in params["properties"]["count"]


def test_responses_wire_and_parse():
    from aegis.providers.responses import DEFAULT_INSTRUCTIONS, ResponsesTransport
    from aegis.types import Message, ToolCall

    t = ResponsesTransport()
    msgs = [
        Message.system("sys"),
        Message.user("hi"),
        Message.assistant("ok", [ToolCall("c1", "read_file", {"path": "a"})]),
        Message.tool("c1", "read_file", "contents"),
    ]
    wire = t._to_wire_input(msgs)
    assert t._instructions(msgs) == "sys"
    assert t._instructions([Message.user("hi")]) == DEFAULT_INSTRUCTIONS
    assert wire[0] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "hi"}],
    }
    assert wire[1]["content"][0]["type"] == "output_text"
    assert wire[2]["type"] == "function_call"
    assert wire[3] == {"type": "function_call_output", "call_id": "c1", "output": "contents"}

    parsed = t._parse_response({
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
            {"type": "function_call", "call_id": "c2", "name": "write_file", "arguments": "{\"path\":\"b\"}"},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    })
    assert parsed.text == "done"
    assert parsed.tool_calls[0].name == "write_file"
    assert parsed.usage.input_tokens == 3


def test_responses_payload_includes_instructions(monkeypatch):
    from aegis.providers.responses import DEFAULT_INSTRUCTIONS, ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("Reply with OK.")],
        tools=None,
        stream=False,
        metadata={"session_id": "sess_meta", "trace_id": "trace_meta", "empty": ""},
        service_tier="priority",
    )

    assert resp.text == "ok"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["json"]["instructions"] == DEFAULT_INSTRUCTIONS
    assert captured["json"]["store"] is False
    assert captured["json"]["metadata"] == {"session_id": "sess_meta", "trace_id": "trace_meta"}
    assert captured["json"]["service_tier"] == "priority"


def test_responses_strips_service_tier_for_xai(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.x.ai/v1",
        auth=FakeAuth(),
        model="grok-4.3",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        service_tier="priority",
    )

    assert "service_tier" not in captured["json"]


def test_responses_records_rate_and_balance_headers(monkeypatch):
    from aegis import ratelimit
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        headers = {"x-balance": "6.25", "x-ratelimit-remaining-requests": "42"}

        def json(self):
            return {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            return FakeResponse()

    ratelimit._latest.clear()
    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
    )

    assert resp.text == "ok"
    assert ratelimit.balance()["provider"] == "openai"
    assert ratelimit.balance()["balance"] == "6.25"
    assert "requests left (min): 42" in ratelimit.summary()


def test_responses_stream_records_rate_and_balance_headers(monkeypatch):
    from aegis import ratelimit
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200
        headers = {"openai-organization-credit-remaining": "5.00"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter([
                'data: {"type":"response.output_text.delta","delta":"o"}',
                'data: {"type":"response.output_text.delta","delta":"k"}',
                'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            return FakeStream()

    ratelimit._latest.clear()
    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=True,
    )

    assert resp.text == "ok"
    assert ratelimit.balance()["provider"] == "openai"
    assert ratelimit.balance()["credits left"] == "5.00"


def test_responses_streams_reasoning_summary_deltas(monkeypatch):
    """The Responses API streams thinking as
    `response.reasoning_summary_text.delta`; the transport must route those
    through `on_reasoning` so live reasoning renders."""
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter([
                'data: {"type":"response.reasoning_summary_text.delta","delta":"Plan"}',
                'data: {"type":"response.reasoning_summary_text.delta","delta":"ning."}',
                'data: {"type":"response.output_text.delta","delta":"Done"}',
                'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            self.payload = json
            FakeClient.last_payload = json
            return FakeStream()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    thoughts: list[str] = []
    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=True,
        reasoning="medium",
        on_reasoning=thoughts.append,
    )

    assert "".join(thoughts) == "Planning."
    assert resp.text == "Done"
    # effort set => we ask codex/openai for a reasoning summary
    assert FakeClient.last_payload["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_responses_retrieve_and_cancel_helpers(monkeypatch):
    from aegis.providers.responses import ResponsesTransport

    calls: list[tuple[str, str, dict]] = []

    class FakeAuth:
        def headers(self):
            return {"Authorization": "Bearer test"}

    class FakeResponse:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, headers):
            calls.append(("GET", url, headers))
            return FakeResponse({"id": "resp_123", "status": "completed"})

        def post(self, url, headers, json):
            calls.append(("POST", url, headers))
            assert json == {}
            return FakeResponse({"id": "resp_123", "status": "cancelled"})

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    transport = ResponsesTransport()

    retrieved = transport.retrieve_response(
        base_url="https://api.openai.com/v1/",
        auth=FakeAuth(),
        response_id="resp_123",
    )
    cancelled = transport.cancel_response(
        base_url="https://api.openai.com/v1/",
        auth=FakeAuth(),
        response_id="resp_123",
    )

    assert retrieved["status"] == "completed"
    assert cancelled["status"] == "cancelled"
    assert calls == [
        (
            "GET",
            "https://api.openai.com/v1/responses/resp_123",
            {"Content-Type": "application/json", "Authorization": "Bearer test"},
        ),
        (
            "POST",
            "https://api.openai.com/v1/responses/resp_123/cancel",
            {"Content-Type": "application/json", "Authorization": "Bearer test"},
        ),
    ]


def test_responses_state_previous_id_and_cache_metrics(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.responses_state import ResponsesStateStore
    from aegis.types import Message

    captured: dict = {}
    ResponsesStateStore().set("sess_state", "resp_prev", provider="openai", model="gpt-5.5")

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "id": "resp_next",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 40, "cache_creation_tokens": 8},
                },
            }

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        session_id="sess_state",
        response_state={"enabled": True, "store": True, "send_previous": True},
    )

    assert captured["json"]["store"] is True
    assert captured["json"]["previous_response_id"] == "resp_prev"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (100, 20)
    assert (resp.usage.cache_read, resp.usage.cache_write) == (40, 8)
    assert ResponsesStateStore().get("sess_state").response_id == "resp_next"


def test_responses_state_not_reused_after_model_or_provider_change(monkeypatch):
    from aegis.agent.loop import _hydrate_previous_response_id
    from aegis.providers.responses import ResponsesTransport
    from aegis.responses_state import ResponsesStateStore
    from aegis.types import Message

    ResponsesStateStore().set("sess_scope", "resp_old", provider="openai", model="gpt-5")

    same = _hydrate_previous_response_id(
        "sess_scope",
        {"enabled": True, "store": True, "send_previous": True},
        provider="openai",
        model="gpt-5",
    )
    changed_model = _hydrate_previous_response_id(
        "sess_scope",
        {"enabled": True, "store": True, "send_previous": True},
        provider="openai",
        model="gpt-5.5",
    )
    changed_provider = _hydrate_previous_response_id(
        "sess_scope",
        {"enabled": True, "store": True, "send_previous": True},
        provider="openai-codex",
        model="gpt-5",
    )

    assert same["previous_response_id"] == "resp_old"
    assert "previous_response_id" not in changed_model
    assert changed_model["previous_response_skipped"] == "provider_or_model_changed"
    assert "previous_response_id" not in changed_provider

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "resp_new",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        session_id="sess_scope",
        response_state={
            "enabled": True,
            "store": True,
            "send_previous": True,
            "provider": "openai",
            "model": "gpt-5.5",
        },
    )

    assert "previous_response_id" not in captured["json"]
    state = ResponsesStateStore().get("sess_scope")
    assert state.response_id == "resp_new"
    assert state.provider == "openai"
    assert state.model == "gpt-5.5"


def test_responses_state_truncates_input_after_previous_response(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.responses_state import ResponsesStateStore
    from aegis.types import Message

    captured: dict = {}
    ResponsesStateStore().set(
        "sess_tail",
        "resp_prev",
        provider="openai",
        model="gpt-5.5",
        input_message_count=2,
    )

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "resp_next",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[
            Message.system("sys"),
            Message.user("old user"),
            Message.assistant("old answer"),
            Message.user("new user"),
        ],
        tools=None,
        stream=False,
        session_id="sess_tail",
        response_state={"enabled": True, "store": True, "send_previous": True},
    )

    assert captured["json"]["previous_response_id"] == "resp_prev"
    assert [item["role"] for item in captured["json"]["input"]] == ["user"]
    assert captured["json"]["input"][0]["content"][0]["text"] == "new user"
    assert ResponsesStateStore().get("sess_tail").input_message_count == 4


def test_responses_state_truncation_keeps_tool_outputs(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.responses_state import ResponsesStateStore
    from aegis.types import Message, ToolCall

    captured: dict = {}
    ResponsesStateStore().set(
        "sess_tool_tail",
        "resp_tool",
        provider="openai",
        model="gpt-5.5",
        input_message_count=2,
    )

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "resp_next_tool",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[
            Message.system("sys"),
            Message.user("old user"),
            Message.assistant("", [ToolCall("call_1", "read_file", {"path": "README.md"})]),
            Message.tool("call_1", "read_file", "file contents"),
        ],
        tools=None,
        stream=False,
        session_id="sess_tool_tail",
        response_state={"enabled": True, "store": True, "send_previous": True},
    )

    assert captured["json"]["input"] == [
        {"type": "function_call_output", "call_id": "call_1", "output": "file contents"}
    ]


def test_responses_state_store_false_is_stateless(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.responses_state import ResponsesStateStore
    from aegis.types import Message

    captured: dict = {}
    ResponsesStateStore().set("sess_stateless", "resp_old", provider="openai", model="gpt-5")

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "resp_new",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        session_id="sess_stateless",
        response_state={"enabled": True, "store": False, "send_previous": True},
    )

    assert captured["json"]["store"] is False
    assert "previous_response_id" not in captured["json"]
    assert ResponsesStateStore().get("sess_stateless").response_id == "resp_old"


def test_responses_state_previous_id_is_trace_metadata():
    from types import SimpleNamespace

    from aegis.agent.loop import _hydrate_previous_response_id, _provider_trace_data
    from aegis.responses_state import ResponsesStateStore

    ResponsesStateStore().set("sess_trace_state", "resp_trace_prev", provider="openai", model="gpt-5")
    state = _hydrate_previous_response_id(
        "sess_trace_state",
        {"enabled": True, "store": True, "send_previous": True},
    )
    agent = SimpleNamespace(
        provider=SimpleNamespace(api_mode="responses", context_length=128),
        budget=SimpleNamespace(api_call_count=0),
        stream=False,
        reasoning="off",
    )

    data = _provider_trace_data(agent, [], [], state, {})

    assert data["responses_state"]["previous_response_id"] == "resp_trace_prev"


def test_responses_context_management_uses_compaction_array(monkeypatch):
    from types import SimpleNamespace

    from aegis.agent.loop import _response_state_for_agent
    from aegis.config import Config
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    cfg = Config.load()
    cfg.data.setdefault("responses", {})["state"] = {"enabled": True, "store": True}
    cfg.data.setdefault("responses", {})["compaction"] = {
        "enabled": True,
        "compact_threshold": 0.5,
    }
    state = _response_state_for_agent(
        SimpleNamespace(
            config=cfg,
            provider=SimpleNamespace(context_length=8000),
        ),
        "sess_context",
    )
    assert state["context_management"] == [{"type": "compaction", "compact_threshold": 4000}]

    cfg_default = Config.load()
    cfg_default.data.setdefault("responses", {})["state"] = {"enabled": True, "store": True}
    cfg_default.data.setdefault("responses", {})["compaction"] = {"enabled": True}
    state_default = _response_state_for_agent(
        SimpleNamespace(
            config=cfg_default,
            provider=SimpleNamespace(context_length=8000),
        ),
        "sess_context_default",
    )
    assert state_default["context_management"] == [{"type": "compaction", "compact_threshold": 4000}]

    cfg_codex = Config.load()
    cfg_codex.data.setdefault("responses", {})["state"] = {"enabled": True, "store": True}
    cfg_codex.data.setdefault("responses", {})["compaction"] = {"enabled": True}
    state_codex = _response_state_for_agent(
        SimpleNamespace(
            config=cfg_codex,
            provider=SimpleNamespace(name="openai-codex", model="gpt-5.5", context_length=8000),
        ),
        "sess_context_codex",
    )
    assert state_codex["context_management"] == [{"type": "compaction", "compact_threshold": 6800}]

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

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

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        session_id="sess_context",
        response_state=state,
    )
    assert captured["json"]["context_management"] == [
        {"type": "compaction", "compact_threshold": 4000}
    ]


def test_responses_stream_reports_active_response_id(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    seen: list[str] = []

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter([
                'data: {"type":"response.created","response":{"id":"resp_stream"}}',
                'data: {"type":"response.output_text.delta","delta":"hi"}',
                'data: {"type":"response.completed","response":{"id":"resp_stream","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"done"}]}]}}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            return FakeStream()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=True,
        on_response_id=seen.append,
    )

    assert resp.text == "done"
    assert seen == ["resp_stream"]


def test_responses_stream_uses_done_output_item_when_completed_output_is_empty(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter([
                'data: {"type":"response.created","response":{"id":"resp_codex"}}',
                'data: {"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Recovered from done item."}]}}',
                'data: {"type":"response.completed","response":{"id":"resp_codex","status":"completed","output":[],"usage":{"output_tokens":5}}}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            return FakeStream()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    resp = ResponsesTransport().complete(
        base_url="https://chatgpt.com/backend-api/codex",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
    )

    assert resp.text == "Recovered from done item."
    assert resp.raw["output"][0]["type"] == "message"


def test_responses_stream_accumulates_function_call_argument_deltas(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_lines(self):
            return iter([
                'data: {"type":"response.created","response":{"id":"resp_tools"}}',
                'data: {"type":"response.output_item.added","output_index":0,"item":{"id":"fc_1","type":"function_call","call_id":"call_1","name":"write_file","arguments":""}}',
                'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","output_index":0,"delta":"{\\"path\\":"}',
                'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","output_index":0,"delta":"\\"a.txt\\"}"}',
                'data: {"type":"response.function_call_arguments.done","item_id":"fc_1","output_index":0,"arguments":"{\\"path\\":\\"a.txt\\"}"}',
                'data: {"type":"response.completed","response":{"id":"resp_tools","status":"completed","output":[]}}',
                "data: [DONE]",
            ])

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, headers, json):
            return FakeStream()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    resp = ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("write")],
        tools=[{
            "name": "write_file",
            "description": "write",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }],
        stream=True,
    )

    assert resp.text == ""
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "call_1"
    assert resp.tool_calls[0].name == "write_file"
    assert resp.tool_calls[0].arguments == {"path": "a.txt"}
    assert resp.raw["output"][0]["arguments"] == '{"path":"a.txt"}'


def test_codex_responses_forces_stream(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import LLMResponse, Message

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    def fake_stream(self, url, headers, payload, on_delta, timeout, on_response_id=None,
                    on_reasoning=None):
        captured["url"] = url
        captured["payload"] = payload
        return LLMResponse(text="ok")

    monkeypatch.setattr("aegis.providers.responses.ResponsesTransport._stream", fake_stream)
    resp = ResponsesTransport().complete(
        base_url="https://chatgpt.com/backend-api/codex",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("Reply OK.")],
        tools=None,
        stream=False,
    )

    assert resp.text == "ok"
    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["store"] is False
    assert "max_output_tokens" not in captured["payload"]


def test_anthropic_coalesces_tool_results():
    from aegis.providers.anthropic import AnthropicTransport
    from aegis.types import Message, ToolCall

    msgs = [
        Message.system("s"),
        Message.user("u1"),
        Message.assistant("", [ToolCall("c1", "t", {}), ToolCall("c2", "t", {})]),
        Message.tool("c1", "t", "r1"),
        Message.tool("c2", "t", "r2"),
    ]
    system, wire = AnthropicTransport()._to_wire(msgs)
    assert system == "s"
    # both tool_results land in one user turn after the assistant
    assert wire[1]["role"] == "assistant"
    results = [b for b in wire[2]["content"] if b["type"] == "tool_result"]
    assert {r["tool_use_id"] for r in results} == {"c1", "c2"}


def test_api_key_auth_schemes(monkeypatch):
    from aegis.providers.auth import ApiKeyAuth
    monkeypatch.setenv("X_KEY", "secret")
    assert ApiKeyAuth(["X_KEY"], "bearer").headers()["Authorization"] == "Bearer secret"
    assert ApiKeyAuth(["X_KEY"], "anthropic").headers()["x-api-key"] == "secret"
    assert ApiKeyAuth([], "none").headers() == {}


def test_api_key_missing_raises():
    from aegis.providers.auth import ApiKeyAuth, AuthError
    with pytest.raises(AuthError):
        ApiKeyAuth(["NOPE_KEY"], "bearer").headers()


def test_credential_pool_rotates(monkeypatch):
    from aegis.providers.auth import ApiKeyAuth
    monkeypatch.setenv("P", "a,b,c")
    auth = ApiKeyAuth(["P"])
    seen = [auth.headers()["Authorization"]]
    for _ in range(3):
        auth.rotate()
        seen.append(auth.headers()["Authorization"])
    assert seen == ["Bearer a", "Bearer b", "Bearer c", "Bearer a"]


def test_registry_builds_and_enforces_64k():
    from aegis.config import Config
    from aegis.providers import build_provider
    cfg = Config.load()
    cfg.data["model"]["provider"] = "anthropic"
    p = build_provider(cfg)
    assert p.context_length >= 64_000
    cfg.data["model"]["context_length"] = 1000
    with pytest.raises(ValueError):
        build_provider(cfg)


def test_provider_count_and_oauth():
    from aegis.providers import list_providers
    from aegis.providers import registry
    assert len(list_providers()) >= 20
    assert all(registry.get_spec(p).oauth for p in ("anthropic", "openai", "openai-codex", "google"))
    assert registry.get_spec("codex").auth_scheme == "codex-backend"


def test_wave3_oauth_catalog_scaffold_is_discoverable():
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    catalog = {row["name"]: row for row in registry.oauth_catalog(cfg)}

    for name in ("qwen", "minimax", "xai", "copilot"):
        assert name in catalog
        assert catalog[name]["oauth_status"] == "planned"

    qwen_spec = registry.get_spec("qwen", cfg)
    dashscope_spec = registry.get_spec("dashscope", cfg)
    assert qwen_spec is not None
    assert dashscope_spec is not None
    assert qwen_spec.base_url == dashscope_spec.base_url
    assert qwen_spec.env_vars == ["QWEN_API_KEY", "DASHSCOPE_API_KEY"]
    assert qwen_spec.oauth is None

    for name in ("qwen", "minimax", "xai"):
        assert catalog[name]["known_provider"] is True
        assert catalog[name]["catalog_only"] is False
        assert catalog[name]["auth_methods"] == ["api_key"]
        assert catalog[name]["oauth"] is False
        assert "API_KEY" in " ".join(catalog[name]["env_vars"])

    assert catalog["copilot"]["known_provider"] is False
    assert catalog["copilot"]["catalog_only"] is True
    assert catalog["copilot"]["auth_methods"] == ["oauth"]

    report = registry.provider_report(cfg)
    provider_rows = {row["name"]: row for row in report["provider_catalog"]}
    assert provider_rows["qwen"]["oauth_status"] == "planned"
    assert provider_rows["minimax"]["oauth_status"] == "planned"
    assert provider_rows["xai"]["oauth_status"] == "planned"
    assert "copilot" not in provider_rows
    assert {row["name"] for row in report["oauth_catalog"]} >= {"qwen", "minimax", "xai", "copilot"}


def test_provider_report_exposes_chain_routing_and_catalog():
    from aegis.config import Config
    from aegis.providers.registry import provider_report

    cfg = Config.load()
    cfg.data["model"] = {"provider": "localtest", "default": "local-model"}
    cfg.data["custom_providers"] = [
        {
            "name": "localtest",
            "base_url": "http://local.test/v1",
            "api_mode": "chat_completions",
            "context_length": 70_000,
        }
    ]
    cfg.data["fallback_providers"] = [{"provider": "ollama", "model": "llama3.1"}]
    cfg.data["routing"] = [{"match": "deploy", "provider": "localtest", "model": "local-routed"}]

    report = provider_report(cfg)

    assert report["active"]["name"] == "localtest"
    assert report["active"]["auth"]["available"] is True
    assert report["active"]["context_length"] == 70_000
    assert report["active"]["capabilities"]["tool_calls"] is True
    assert report["active"]["capabilities"]["images"] is False
    assert [row["name"] for row in report["chain"]] == ["localtest", "ollama"]
    assert [row["provider"] for row in report["fallback_chain"]] == ["localtest", "ollama"]
    assert report["fallbacks"][0]["role"] == "fallback:1"
    assert report["fallbacks"][0]["capabilities"]["tool_calls"] is True
    assert report["routing"][0]["known_provider"] is True
    assert report["routing"][0]["capability_summary"]
    custom = next(row for row in report["provider_catalog"] if row["name"] == "localtest")
    assert custom["origin"] == "custom"
    assert custom["base_url"] == "http://local.test/v1"
    openai = next(row for row in report["provider_catalog"] if row["name"] == "openai")
    assert openai["capabilities"]["reasoning_effort"] is True
    assert openai["capabilities"]["reasoning_stream"] is True
    assert openai["capabilities"]["images"] is True
    assert openai["capabilities"]["fast_mode"] is True

    xai = next(row for row in report["provider_catalog"] if row["name"] == "xai")
    assert xai["capabilities"]["fast_mode"] is False


def test_model_fast_mode_capabilities_are_provider_aware():
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    anthropic_rows = {row["id"]: row for row in registry.known_model_entries_for("anthropic", cfg)}
    assert anthropic_rows["claude-opus-4-6"]["capabilities"]["fast_mode"] is True
    assert anthropic_rows["claude-opus-4-8"]["capabilities"]["fast_mode"] is False

    openrouter_rows = {
        row["id"]: row for row in registry.known_model_entries_for("openrouter", cfg)
    }
    assert openrouter_rows["anthropic/claude-sonnet-4.5"]["capabilities"]["fast_mode"] is False


def test_glm_52_is_in_zai_and_openrouter_catalogs():
    from aegis import model_meta
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    zai_rows = {row["id"]: row for row in registry.known_model_entries_for("zai", cfg)}
    assert list(zai_rows)[0] == "glm-5.2"
    assert zai_rows["glm-5.2"]["source"] == "default"
    assert zai_rows["glm-5.2"]["context_length"] == 1_048_576
    assert zai_rows["glm-4.6"]["context_length"] == 128_000

    openrouter_rows = {row["id"]: row for row in registry.known_model_entries_for("openrouter", cfg)}
    assert openrouter_rows["z-ai/glm-5.2"]["context_length"] == 1_048_576
    assert model_meta.context_window("z-ai/glm-5.2", provider="openrouter") == 1_048_576


def test_xai_defaults_to_grok_build_catalog():
    from aegis import model_meta
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    xai_rows = {row["id"]: row for row in registry.known_model_entries_for("xai", cfg)}

    assert registry.PROVIDERS["xai"].default_model == "grok-build-0.1"
    assert list(xai_rows)[0] == "grok-build-0.1"
    assert xai_rows["grok-build-0.1"]["source"] == "default"
    assert xai_rows["grok-4.3"]["context_length"] == 256_000
    assert model_meta.context_window("grok-build-0.1", provider="xai") == 131_072


def test_model_inventory_dedupes_presets_and_keeps_provider_ownership():
    from aegis.config import Config
    from aegis.providers import registry

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

    openai_rows = registry.known_model_entries_for("openai", cfg)
    assert openai_rows[0]["id"] == "gpt-5.5"
    assert openai_rows[0]["source"] == "default"
    assert len({row["id"].lower() for row in openai_rows}) == len(openai_rows)

    inventory = registry.model_inventory(cfg, ["openai", "mirror"])
    assert any(row["provider"] == "openai" and row["id"] == "gpt-5.5" for row in inventory)
    assert any(row["provider"] == "mirror" and row["id"] == "gpt-5.5" for row in inventory)


def test_custom_provider_model_catalog_keeps_default_first_and_dedupes():
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    cfg.data["custom_providers"] = [
        {
            "name": "localtest",
            "base_url": "http://local.test/v1",
            "api_mode": "chat_completions",
            "default_model": "local-default",
            "context_length": 70_000,
            "models": ["LOCAL-DEFAULT", "local-preview", "local-live-only", "local-preview"],
        }
    ]

    rows = registry.picker_model_entries_for("localtest", cfg)
    assert [row["id"] for row in rows] == ["local-default", "local-preview", "local-live-only"]
    assert rows[0]["source"] == "default"
    assert rows[1]["source"] == "catalog"

    validation = registry.validate_model_choice("localtest", "local-preview", cfg)
    assert validation["ok"] is True
    assert validation["model_known"] is True

    report = registry.provider_report(cfg)
    custom = next(row for row in report["custom_providers"] if row["name"] == "localtest")
    assert custom["models"] == ["LOCAL-DEFAULT", "local-preview", "local-live-only"]


def test_picker_model_entries_filter_custom_models_from_aggregators():
    from aegis.config import Config
    from aegis.providers import registry

    cfg = Config.load()
    cfg.data["custom_providers"] = [
        {
            "name": "litellm-proxy",
            "base_url": "http://proxy.local/v1",
            "api_mode": "chat_completions",
            "default_model": "ANTHROPIC/CLAUDE-SONNET-4.5",
            "context_length": 70_000,
        }
    ]

    inventory = registry.model_inventory(cfg, ["openrouter", "litellm-proxy"])
    rows = {(row["provider"], row["id"].lower()) for row in inventory}
    assert ("litellm-proxy", "anthropic/claude-sonnet-4.5") in rows
    assert ("openrouter", "anthropic/claude-sonnet-4.5") in rows

    picker_rows = registry.picker_model_entries_for("openrouter", cfg)
    picker_ids = {row["id"].lower() for row in picker_rows}
    assert "anthropic/claude-sonnet-4.5" not in picker_ids
    assert picker_rows

    custom_picker_ids = {row["id"].lower() for row in registry.picker_model_entries_for("litellm-proxy", cfg)}
    assert "anthropic/claude-sonnet-4.5" in custom_picker_ids


def test_model_validation_warns_with_suggestions_without_blocking_custom():
    from aegis.config import Config
    from aegis.providers.registry import (
        model_validation_message,
        provider_report,
        validate_model_choice,
    )

    cfg = Config.load()
    typo = validate_model_choice("anthropic", "claude-sonet-4-6", cfg)
    assert typo["ok"] is True
    assert typo["model_known"] is False
    assert "claude-sonnet-4-6" in typo["model_suggestions"]
    assert "Closest known models" in model_validation_message(typo)

    cfg.data["custom_providers"] = [
        {
            "name": "localtest",
            "base_url": "http://local.test/v1",
            "api_mode": "chat_completions",
            "context_length": 70_000,
        }
    ]
    custom = validate_model_choice("localtest", "fresh-local-model", cfg)
    assert custom["ok"] is True
    assert custom["custom_allowed"] is True
    assert not custom.get("warning")

    cfg.data["model"] = {"provider": "anthropic", "default": "claude-sonet-4-6"}
    report = provider_report(cfg)
    assert "claude-sonnet-4-6" in report["active"]["warning"]
    assert report["active"]["model_validation"]["model_suggestions"]


def test_model_validation_rejects_unknown_provider_with_suggestion():
    from aegis.config import Config
    from aegis.providers.registry import model_validation_message, validate_model_choice

    validation = validate_model_choice("anthropc", "claude-sonnet-4-6", Config.load())

    assert validation["ok"] is False
    assert validation["provider_known"] is False
    assert "anthropic" in validation["provider_suggestions"]
    assert "Unknown provider 'anthropc'" in model_validation_message(validation)


def test_openai_codex_builds_oauth_responses_provider():
    from aegis.config import Config
    from aegis.providers import build_provider
    from aegis.providers.base import ApiMode
    from aegis.providers.responses import ResponsesTransport

    cfg = Config.load()
    cfg.data["model"]["provider"] = "openai-codex"
    cfg.data["model"]["default"] = "gpt-5.5"
    provider = build_provider(cfg)

    assert provider.api_mode == ApiMode.RESPONSES
    assert isinstance(provider.transport, ResponsesTransport)
    assert provider.auth.describe() == "oauth (openai-codex: not logged in)"
    assert provider.base_url == "https://chatgpt.com/backend-api/codex"
    assert provider.context_length == 272_000


def test_codex_builds_stateless_backend_responses_provider(monkeypatch, tmp_path):
    from aegis.config import Config
    from aegis.providers import build_provider
    from aegis.providers.base import ApiMode
    from aegis.providers.responses import ResponsesTransport

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    cfg = Config.load()
    cfg.data["model"]["provider"] = "codex"
    cfg.data["model"]["default"] = "gpt-5.5"
    provider = build_provider(cfg)

    assert provider.api_mode == ApiMode.RESPONSES
    assert isinstance(provider.transport, ResponsesTransport)
    assert provider.auth.describe() == "codex-backend (run `codex login`)"
    assert provider.base_url == "https://chatgpt.com/backend-api/codex"
    assert provider.context_length == 272_000


def test_codex_app_server_builds_app_server_provider(monkeypatch):
    from aegis.config import Config
    from aegis.providers import build_provider
    from aegis.providers.base import ApiMode
    from aegis.providers.codex_app_server import CodexAppServerTransport

    class Status:
        returncode = 0
        stdout = "Logged in using ChatGPT"
        stderr = ""

    monkeypatch.setattr("aegis.providers.auth.shutil.which", lambda _cmd: "/bin/codex")
    monkeypatch.setattr("aegis.providers.auth.subprocess.run", lambda *_args, **_kwargs: Status())

    cfg = Config.load()
    cfg.data["model"]["provider"] = "codex-app-server"
    cfg.data["model"]["default"] = "gpt-5.5"
    provider = build_provider(cfg)

    assert provider.api_mode == ApiMode.CODEX_APP_SERVER
    assert isinstance(provider.transport, CodexAppServerTransport)
    assert provider.auth.describe() == "codex-cli (ChatGPT login ready)"
    assert provider.base_url == "codex://app-server"
    assert provider.context_length == 272_000


def test_openai_and_codex_context_windows_do_not_drift():
    from aegis.config import Config
    from aegis.providers import build_provider
    from aegis.providers.registry import known_model_entries_for, provider_report

    cfg = Config.load()
    cfg.data["model"]["provider"] = "openai"
    cfg.data["model"]["default"] = "gpt-5.5"
    assert build_provider(cfg).context_length == 1_050_000
    cfg.data["model"]["default"] = "gpt-5.4-mini"
    assert build_provider(cfg).context_length == 400_000

    cfg.data["model"]["provider"] = "openai-codex"
    cfg.data["model"]["default"] = "gpt-5.4-mini"
    assert build_provider(cfg).context_length == 272_000

    openai_models = {row["id"]: row["context_length"] for row in known_model_entries_for("openai", cfg)}
    codex_models = {row["id"]: row["context_length"] for row in known_model_entries_for("openai-codex", cfg)}
    assert openai_models["gpt-5.5"] == 1_050_000
    assert codex_models["gpt-5.4-mini"] == 272_000

    report = provider_report(cfg)
    specs = {row["name"]: row for row in report["provider_catalog"]}
    assert specs["openai"]["context_length"] == 1_050_000
    assert specs["openai-codex"]["context_length"] == 272_000


def test_codex_app_server_projects_dynamic_tools():
    from aegis.providers.codex_app_server import CodexAppServerTransport

    tools = CodexAppServerTransport()._to_dynamic_tools([
        {
            "name": "system_status",
            "description": "Inspect install state",
            "parameters": {"type": "object", "properties": {"verbose": {"type": "boolean"}}},
        }
    ])

    assert tools == [
        {
            "name": "system_status",
            "namespace": "aegis",
            "description": "Inspect install state",
            "inputSchema": {"type": "object", "properties": {"verbose": {"type": "boolean"}}},
        }
    ]


def test_codex_app_server_handles_dynamic_tool_request():
    from aegis.providers.codex_app_server import CodexAppServerTransport
    from aegis.tools.base import ToolResult

    class Client:
        def __init__(self):
            self.result = None
            self.error = None

        def respond(self, _request_id, result=None):
            self.result = result

        def respond_error(self, _request_id, message, code=-32603):
            self.error = (code, message)

    client = Client()
    transport = CodexAppServerTransport()
    transport._client = client

    seen = []

    def run(call):
        seen.append(call)
        return ToolResult.ok("tool output")

    transport._handle_server_request(
        {
            "id": 7,
            "method": "item/tool/call",
            "params": {
                "callId": "call_1",
                "tool": "system_status",
                "arguments": {"verbose": True},
                "threadId": "thr",
                "turnId": "turn",
            },
        },
        tool_runner=run,
        approver=None,
    )

    assert seen[0].name == "system_status"
    assert seen[0].arguments == {"verbose": True}
    assert client.result == {
        "contentItems": [{"type": "inputText", "text": "tool output"}],
        "success": True,
    }
    assert client.error is None


def test_codex_app_server_streams_reasoning_deltas():
    """Codex emits its thinking as `item/reasoning/summaryTextDelta`
    notifications; the transport must surface them through `on_reasoning` so
    the display can render the live reasoning box."""
    from aegis.providers.codex_app_server import CodexAppServerTransport
    from aegis.types import Message

    class FakeClient:
        def __init__(self, messages):
            self._messages = list(messages)
            self.requests = []

        def request(self, method, params, timeout=20, server_request_handler=None):
            self.requests.append((method, params))
            return {"turn": {"id": "turn_1"}}

        def is_alive(self):
            return True

        def stderr_tail(self):
            return ""

        def take_message(self, timeout=0.25):
            return self._messages.pop(0) if self._messages else None

    transport = CodexAppServerTransport()
    transport._thread_id = "thr_1"
    transport._client = FakeClient([
        {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "Let me "}},
        {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "think."}},
        {"method": "item/agentMessage/delta", "params": {"delta": "Hi."}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "Hi."}}},
        {"method": "turn/completed", "params": {"turn": {"id": "turn_1"}}},
    ])
    transport._ensure_thread = lambda **_kw: transport._client

    thoughts: list[str] = []
    text: list[str] = []
    resp = transport.complete(
        base_url="codex://app-server",
        auth=type("A", (), {"available": lambda self: True})(),
        model="gpt-5.5",
        messages=[Message(role="user", content="hello")],
        tools=None,
        stream=True,
        on_delta=text.append,
        on_reasoning=thoughts.append,
        reasoning="high",
    )

    assert "".join(thoughts) == "Let me think."
    assert "".join(text) == "Hi."
    assert resp.text == "Hi."
    method, params = transport._client.requests[-1]
    assert method == "turn/start"
    assert params["effort"] == "high"
    assert params["summary"] == "auto"


def test_codex_app_server_request_does_not_replay_notifications():
    from collections import deque
    import queue

    from aegis.providers.codex_app_server import _CodexAppServerClient

    client = _CodexAppServerClient.__new__(_CodexAppServerClient)
    client._next_id = 1
    client._incoming = queue.Queue()
    client._backlog = deque([
        {"method": "remoteControl/status/changed", "params": {"status": "disabled"}},
        {"id": 1, "result": {"ok": True}},
    ])
    sent = []
    client._send = lambda msg: sent.append(msg)
    client.is_alive = lambda: True
    client.stderr_tail = lambda: ""

    result = _CodexAppServerClient.request(client, "thread/start", {}, timeout=1)

    assert result == {"ok": True}
    assert sent[0]["method"] == "thread/start"
    assert list(client._backlog) == [
        {"method": "remoteControl/status/changed", "params": {"status": "disabled"}}
    ]


def test_openai_codex_oauth_adds_account_header():
    import base64
    import json
    import time

    from aegis.providers.auth import AuthStore, OAuthAuth
    from aegis.providers.registry import OPENAI_CODEX_OAUTH

    def enc(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    token = enc({"alg": "none"}) + "." + enc({"chatgpt_account_id": "acct_123"}) + ".sig"
    store = AuthStore()
    store.save("openai-codex", {
        "access_token": token,
        "refresh_token": "refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "quarantined": False,
    })

    headers = OAuthAuth(OPENAI_CODEX_OAUTH, store).headers()
    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["chatgpt-account-id"] == "acct_123"


def test_openai_api_key_wins_over_identity_only_oauth(monkeypatch):
    from aegis.config import Config
    from aegis.providers import build_provider, registry
    from aegis.providers.auth import AuthError, AuthStore, OAuthAuth

    cfg = Config.load()
    cfg.data["model"]["provider"] = "openai"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    AuthStore().save("openai", {
        "access_token": "opaque",
        "token_type": "Bearer",
        "scope": "openid profile email offline_access",
        "quarantined": False,
    })

    provider = build_provider(cfg)
    assert provider.auth.describe().startswith("api-key")

    oauth = OAuthAuth(registry.get_spec("openai").oauth, AuthStore())
    assert oauth.missing_required_scopes() == ["model.request"]
    assert not oauth.available()
    with pytest.raises(AuthError, match="missing required API scope"):
        oauth.headers()


def test_fallback_provider_retries():
    from aegis.providers.fallback import FallbackProvider
    from aegis.types import LLMResponse

    class Down:
        context_length = 64_000
        name = "d"
        model = "m"
        api_mode = None
        auth = None

        def describe(self): return "d"
        def complete(self, *a, **k): raise RuntimeError("boom")

    class Up(Down):
        def complete(self, *a, **k): return LLMResponse(text="ok")

    assert FallbackProvider(Down(), [Up()]).complete([]).text == "ok"


def test_fallback_provider_uses_active_first_after_failover():
    from aegis.providers.fallback import FallbackProvider
    from aegis.types import LLMResponse

    class Provider:
        context_length = 64_000
        model = "m"
        api_mode = None
        auth = None

        def __init__(self, name, *, fail=False):
            self.name = name
            self.fail = fail
            self.calls = 0

        def describe(self):
            return self.name

        def complete(self, *a, **k):
            self.calls += 1
            if self.fail:
                raise RuntimeError("down")
            return LLMResponse(text=self.name)

    primary = Provider("primary", fail=True)
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert provider.complete([]).text == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 2


def test_fallback_provider_delegates_cancel_to_active_provider():
    from aegis.providers.fallback import FallbackProvider

    class Provider:
        context_length = 64_000
        model = "m"
        api_mode = None
        auth = None

        def __init__(self, name):
            self.name = name
            self.cancelled = []

        def describe(self):
            return self.name

        def complete(self, *a, **k):
            raise RuntimeError("not used")

        def cancel_response(self, response_id):
            self.cancelled.append(response_id)
            return {"cancelled": response_id, "provider": self.name}

    primary = Provider("primary")
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])
    provider.active = fallback

    assert provider.cancel_response("resp_active") == {
        "cancelled": "resp_active",
        "provider": "fallback",
    }
    assert primary.cancelled == []
    assert fallback.cancelled == ["resp_active"]


def test_reasoning_threads_to_provider():
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from conftest import FakeProvider
    cfg = Config.load()
    cfg.data["agent"]["reasoning_effort"] = "high"
    fp = FakeProvider()
    agent = Agent(config=cfg, provider=fp, session=Session.create())
    agent.run("hi")
    assert fp.last_reasoning == "high"


def test_codex_backend_sends_session_cache_headers(monkeypatch):
    """The Codex backend routes prompt-cache scope by request headers; a stable
    session id must ride along as `session_id` / `x-client-request-id` so cache
    hits stay high across the many turns of one run."""
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeStream:
        status_code = 200
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def iter_lines(self):
            yield ('data: {"type":"response.completed","response":{"output":'
                   '[{"type":"message","content":[{"type":"output_text","text":"ok"}]}],'
                   '"status":"completed","usage":{"input_tokens":5,"output_tokens":1}}}')
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def stream(self, method, url, headers, json):
            captured["headers"] = headers
            return FakeStream()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://chatgpt.com/backend-api/codex",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        session_id="sess-abc-123",
    )

    assert captured["headers"]["session_id"] == "sess-abc-123"
    assert captured["headers"]["x-client-request-id"] == "sess-abc-123"


def test_non_codex_backend_omits_session_headers(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def post(self, url, headers, json):
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)
    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=FakeAuth(),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        session_id="sess-abc-123",
    )

    assert "session_id" not in captured["headers"]
    assert "x-client-request-id" not in captured["headers"]


def test_structured_output_response_format_mapping():
    """Chat-style response_format maps to the Responses API text.format shape."""
    from aegis.providers.responses import _to_text_format

    chat = {"type": "json_schema",
            "json_schema": {"name": "Person", "schema": {"type": "object"}, "strict": True}}
    out = _to_text_format(chat)
    assert out == {"type": "json_schema", "name": "Person",
                   "schema": {"type": "object"}, "strict": True}
    assert _to_text_format({"type": "json_object"}) == {"type": "json_object"}


def test_structured_output_capability_per_api_mode():
    """OpenAI-family modes advertise structured_output; codex app-server does not."""
    from aegis.providers.base import ApiMode
    from aegis.providers.registry import _model_capabilities, _normalized_capabilities

    chat = _normalized_capabilities(_model_capabilities("gpt-x", ApiMode.CHAT_COMPLETIONS))
    codex = _normalized_capabilities(_model_capabilities("gpt-x", ApiMode.CODEX_APP_SERVER))
    assert chat["structured_output"] is True
    assert codex["structured_output"] is False
