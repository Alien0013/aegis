"""OpenAI Responses API transport.

This covers both the public OpenAI Responses endpoint and the advanced direct
Codex/ChatGPT backend. Normal ChatGPT subscription auth uses the Codex
app-server transport instead.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from .auth import AuthProvider
from .base import ApiMode, OnDelta, ProviderTransport
from .chat_completions import _raise_for_status
from .schema import sanitize as _sanitize_schema

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
                "parameters": _sanitize_schema(
                    t.get("parameters", {"type": "object", "properties": {}})),
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
        tool_runner=None,
        approver=None,
        cwd=None,
        session_id: str | None = None,
        response_state: dict | None = None,
        metadata: dict | None = None,
        on_response_id=None,
        on_reasoning: OnDelta | None = None,
    ) -> LLMResponse:
        url = f"{base_url}/responses"
        headers = {"Content-Type": "application/json", **(extra_headers or {}), **auth.headers()}
        is_codex_backend = self._requires_stream(base_url)
        wire_stream = stream or is_codex_backend
        state = response_state or {}
        store_response = bool(state.get("enabled") and state.get("store"))
        previous_response_id = str(state.get("previous_response_id") or "").strip()
        previous_state = None
        wire_messages = list(messages)
        payload: dict[str, Any] = {
            "model": model,
            "instructions": self._instructions(messages),
            "stream": wire_stream,
            "store": store_response,
        }
        if state.get("enabled") and state.get("send_previous", True) and store_response and session_id:
            try:
                from ..responses_state import ResponsesStateStore
                previous_state = ResponsesStateStore().get(session_id)
            except Exception:  # noqa: BLE001
                previous_state = None
            if previous_state and not self._state_matches_current(previous_state, state, model):
                previous_state = None
                previous_response_id = ""
            if not previous_response_id and previous_state and previous_state.response_id:
                previous_response_id = previous_state.response_id
            if previous_response_id:
                payload["previous_response_id"] = previous_response_id
                if state.get("truncate_previous_input", True):
                    wire_messages = self._tail_after_previous_response(
                        messages,
                        previous_response_id=previous_response_id,
                        previous_state=previous_state,
                    )
        payload["input"] = self._to_wire_input(wire_messages)
        clean_metadata = self._metadata(metadata)
        if clean_metadata:
            payload["metadata"] = clean_metadata
        context_management = self._context_management(
            state.get("context_management") or state.get("compaction")
        )
        if context_management:
            payload["context_management"] = context_management
        if not is_codex_backend:
            payload["max_output_tokens"] = max_tokens
        eff = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
               "xhigh": "high"}.get(reasoning)
        if eff:
            # Request a reasoning summary so the model streams visible thinking
            # (`response.reasoning_summary_text.delta`) when a live display wants it.
            payload["reasoning"] = {"effort": eff, "summary": "auto"}
        wire_tools = self._to_wire_tools(tools)
        if wire_tools:
            payload["tools"] = wire_tools

        if wire_stream:
            resp = self._stream(url, headers, payload, on_delta, timeout, on_response_id, on_reasoning)
        else:
            resp = self._blocking(url, headers, payload, timeout)
            if on_response_id and isinstance(resp.raw, dict) and resp.raw.get("id"):
                on_response_id(str(resp.raw["id"]))
        self._remember_response(session_id, response_state or {}, model, resp, len(messages))
        return resp

    def _metadata(self, metadata: dict | None) -> dict[str, str]:
        if not isinstance(metadata, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            name = str(key).strip()
            text = str(value).strip()
            if name and text:
                out[name[:64]] = text[:512]
        return out

    def _context_management(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict) and value:
            return [dict(value)]
        return []

    def _state_matches_current(self, previous_state: Any, state: dict[str, Any], model: str) -> bool:
        provider = str(state.get("provider") or "")
        expected_model = str(state.get("model") or model or "")
        previous_provider = str(getattr(previous_state, "provider", "") or "")
        previous_model = str(getattr(previous_state, "model", "") or "")
        if provider and previous_provider and provider != previous_provider:
            return False
        if expected_model and previous_model and expected_model != previous_model:
            return False
        return True

    def _tail_after_previous_response(
        self,
        messages: list[Message],
        *,
        previous_response_id: str,
        previous_state: Any,
    ) -> list[Message]:
        if not previous_state or previous_state.response_id != previous_response_id:
            return list(messages)
        start = int(getattr(previous_state, "input_message_count", 0) or 0)
        if start <= 0 or start >= len(messages):
            return list(messages)
        tail = list(messages[start:])
        while tail and tail[0].role == "assistant":
            tail.pop(0)
        return tail or list(messages)

    def _remember_response(
        self,
        session_id: str | None,
        state: dict[str, Any],
        model: str,
        resp: LLMResponse,
        input_message_count: int,
    ) -> None:
        if not (state.get("enabled") and state.get("store") and session_id and isinstance(resp.raw, dict)):
            return
        rid = resp.raw.get("id")
        if not rid:
            return
        output_items = resp.raw.get("output") if state.get("preserve_items", True) else []
        try:
            from ..responses_state import ResponsesStateStore
            ResponsesStateStore().set(
                session_id,
                rid,
                provider=str(state.get("provider") or "responses"),
                model=str(state.get("model") or model or ""),
                output_items=output_items if isinstance(output_items, list) else [],
                input_message_count=input_message_count,
            )
        except Exception:  # noqa: BLE001
            pass

    def _blocking(self, url, headers, payload, timeout) -> LLMResponse:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
        _raise_for_status(r)
        from .. import ratelimit
        ratelimit.record_response_headers(getattr(r, "headers", {}), base_url=url)
        data = r.json()
        return self._parse_response(data)

    def _stream(self, url, headers, payload, on_delta, timeout, on_response_id=None,
                on_reasoning=None) -> LLMResponse:
        text_parts: list[str] = []
        completed: dict[str, Any] | None = None
        seen_response_ids: set[str] = set()
        stream_timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)
        with httpx.Client(timeout=stream_timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as r:
                _raise_for_status(r)
                from .. import ratelimit
                ratelimit.record_response_headers(getattr(r, "headers", {}), base_url=url)
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
                    self._notify_response_id(event, on_response_id, seen_response_ids)
                    etype = event.get("type") or ""
                    if etype == "response.output_text.delta":
                        delta = event.get("delta") or ""
                        text_parts.append(delta)
                        if on_delta:
                            on_delta(delta)
                    elif etype.startswith("response.reasoning") and etype.endswith(".delta"):
                        # response.reasoning_summary_text.delta (and raw
                        # reasoning_text.delta) carry the model's live thinking.
                        delta = event.get("delta") or ""
                        if delta and on_reasoning:
                            on_reasoning(delta)
                    elif event.get("type") == "response.completed":
                        completed = event.get("response") or {}
                        self._notify_response_id(completed, on_response_id, seen_response_ids)
        if completed:
            parsed = self._parse_response(completed)
            if parsed.text:
                return parsed
            parsed.text = "".join(text_parts)
            return parsed
        return LLMResponse(text="".join(text_parts))

    def _notify_response_id(self, event: dict[str, Any], on_response_id, seen: set[str]) -> None:
        if not on_response_id or not isinstance(event, dict):
            return
        rid = ""
        response = event.get("response")
        if isinstance(response, dict):
            rid = str(response.get("id") or "")
        rid = rid or str(event.get("response_id") or "")
        if not rid and event.get("object") == "response":
            rid = str(event.get("id") or "")
        if not rid or rid in seen:
            return
        seen.add(rid)
        try:
            on_response_id(rid)
        except Exception:  # noqa: BLE001
            pass

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
            usage=self._parse_usage(usage),
            raw=data,
        )

    def _parse_usage(self, usage: dict[str, Any]) -> Usage:
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cache_read = (
            details.get("cached_tokens")
            or details.get("cache_read_tokens")
            or usage.get("cache_read_input_tokens")
            or 0
        )
        cache_write = (
            details.get("cache_creation_tokens")
            or details.get("cache_write_tokens")
            or usage.get("cache_creation_input_tokens")
            or 0
        )
        return Usage(input_tokens, output_tokens, cache_read, cache_write)

    def retrieve_response(
        self,
        *,
        base_url: str,
        auth: AuthProvider,
        response_id: str,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", **(extra_headers or {}), **auth.headers()}
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{base_url.rstrip('/')}/responses/{response_id}", headers=headers)
        _raise_for_status(r)
        return r.json()

    def cancel_response(
        self,
        *,
        base_url: str,
        auth: AuthProvider,
        response_id: str,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", **(extra_headers or {}), **auth.headers()}
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base_url.rstrip('/')}/responses/{response_id}/cancel",
                headers=headers,
                json={},
            )
        _raise_for_status(r)
        return r.json()
