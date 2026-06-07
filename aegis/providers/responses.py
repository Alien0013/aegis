"""OpenAI Responses API transport.

This covers both the public OpenAI Responses endpoint and the Codex/ChatGPT
backend used by ChatGPT OAuth installs.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from .auth import AuthProvider
from .base import ApiMode, OnDelta, ProviderTransport
from .chat_completions import _raise_for_status

DEFAULT_INSTRUCTIONS = "You are AEGIS, a careful coding agent. Follow the user's instructions."


class ResponsesTransport(ProviderTransport):
    api_mode = ApiMode.RESPONSES

    # -- wire conversion ----------------------------------------------------
    def _requires_stream(self, base_url: str) -> bool:
        return "chatgpt.com/backend-api/codex" in base_url.rstrip("/")

    def _instructions(self, messages: list[Message]) -> str:
        instructions = "\n\n".join(m.content for m in messages if m.role == "system" and m.content)
        return instructions or DEFAULT_INSTRUCTIONS

    def _to_wire_input(self, messages: list[Message]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": m.tool_call_id or "",
                        "output": m.content,
                    }
                )
                continue
            if m.role == "assistant" and m.tool_calls:
                if m.content:
                    items.append(self._message_item("assistant", m.content))
                for tc in m.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        }
                    )
                continue
            if m.images:
                items.append(self._image_message_item(m))
                continue
            items.append(self._message_item(m.role, m.content))
        return items

    def _message_item(self, role: str, text: str) -> dict[str, Any]:
        content_type = "output_text" if role == "assistant" else "input_text"
        return {
            "type": "message",
            "role": role,
            "content": [{"type": content_type, "text": text}],
        }

    def _image_message_item(self, message: Message) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": message.content}]
        for image in message.images:
            content.append({"type": "input_image", "image_url": image})
        return {"type": "message", "role": message.role, "content": content}

    def _to_wire_tools(self, tools: list[ToolSchema] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                "strict": False,
            }
            for t in tools
        ]

    # -- request ------------------------------------------------------------
    def complete(
        self,
        *,
        base_url: str,
        auth: AuthProvider,
        model: str,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        stream: bool,
        on_delta: OnDelta | None = None,
        max_tokens: int = 8192,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 600.0,
        reasoning: str = "off",
    ) -> LLMResponse:
        url = f"{base_url}/responses"
        headers = {"Content-Type": "application/json", **(extra_headers or {}), **auth.headers()}
        is_codex_backend = self._requires_stream(base_url)
        wire_stream = stream or is_codex_backend
        payload: dict[str, Any] = {
            "model": model,
            "instructions": self._instructions(messages),
            "input": self._to_wire_input(messages),
            "stream": wire_stream,
            "store": False,
        }
        if not is_codex_backend:
            payload["max_output_tokens"] = max_tokens
        eff = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
               "xhigh": "high"}.get(reasoning)
        if eff:
            payload["reasoning"] = {"effort": eff}
        wire_tools = self._to_wire_tools(tools)
        if wire_tools:
            payload["tools"] = wire_tools

        if wire_stream:
            return self._stream(url, headers, payload, on_delta, timeout)
        return self._blocking(url, headers, payload, timeout)

    def _blocking(self, url, headers, payload, timeout) -> LLMResponse:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
        _raise_for_status(r)
        data = r.json()
        return self._parse_response(data)

    def _stream(self, url, headers, payload, on_delta, timeout) -> LLMResponse:
        text_parts: list[str] = []
        completed: dict[str, Any] | None = None
        stream_timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)
        with httpx.Client(timeout=stream_timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as r:
                _raise_for_status(r)
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "response.output_text.delta":
                        delta = event.get("delta") or ""
                        text_parts.append(delta)
                        if on_delta:
                            on_delta(delta)
                    elif event.get("type") == "response.completed":
                        completed = event.get("response") or {}
        if completed:
            parsed = self._parse_response(completed)
            if parsed.text:
                return parsed
            parsed.text = "".join(text_parts)
            return parsed
        return LLMResponse(text="".join(text_parts))

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for item in data.get("output") or []:
            typ = item.get("type")
            if typ == "message":
                for part in item.get("content") or []:
                    if part.get("type") in {"output_text", "text"} and part.get("text"):
                        text_parts.append(part["text"])
            elif typ == "function_call":
                tool_calls.append(
                    ToolCall.from_json_args(
                        item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}",
                        item.get("name") or "",
                        item.get("arguments") or "",
                    )
                )
        usage = data.get("usage") or {}
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=data.get("status"),
            usage=Usage(
                usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                usage.get("output_tokens", usage.get("completion_tokens", 0)),
            ),
            raw=data,
        )
