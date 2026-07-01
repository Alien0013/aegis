"""OpenAI Responses API transport.

This covers both the public OpenAI Responses endpoint and the direct
Codex/ChatGPT backend. The default ChatGPT subscription path uses this
transport with ``store: false``; the Codex app-server runtime is an explicit
opt-in provider.
"""

from __future__ import annotations

import json
import hashlib
from typing import Any

import httpx

from ..types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from .auth import AuthProvider
from .base import ApiMode, OnDelta, ProviderTransport
from .chat_completions import (
    _raise_for_status,
    apply_request_overrides,
    request_extra_headers,
    request_timeout,
)
from .schema import sanitize as _sanitize_schema

DEFAULT_INSTRUCTIONS = "You are AEGIS, a careful coding agent. Follow the user's instructions."


def _to_text_format(response_format: dict) -> dict:
    """Map a chat-style ``response_format`` to the Responses API ``text.format`` shape.

    Chat ``{"type":"json_schema","json_schema":{"name","schema","strict"}}`` flattens to
    ``{"type":"json_schema","name","schema","strict"}``; ``json_object`` passes through.
    Anything already in ``text.format`` shape (schema/name at top level) is returned as-is.
    """
    fmt = dict(response_format or {})
    if fmt.get("type") == "json_schema" and isinstance(fmt.get("json_schema"), dict):
        inner = fmt["json_schema"]
        out = {"type": "json_schema"}
        for key in ("name", "schema", "strict", "description"):
            if key in inner:
                out[key] = inner[key]
        return out
    return fmt


def _content_cache_key(instructions: str, tools: list[dict[str, Any]] | None) -> str:
    if not instructions and not tools:
        return ""
    raw = json.dumps(
        {"instructions": instructions or "", "tools": tools or []},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


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
        service_tier: str = "",
        response_format: dict | None = None,
        request_overrides: dict | None = None,
    ) -> LLMResponse:
        url = f"{base_url}/responses"
        headers = {
            "Content-Type": "application/json",
            **(extra_headers or {}),
            **request_extra_headers(request_overrides),
            **auth.headers(),
        }
        timeout = request_timeout(timeout, request_overrides)
        is_codex_backend = self._requires_stream(base_url)
        is_xai = self._is_xai_responses(base_url, model)
        wire_stream = stream or is_codex_backend
        if is_codex_backend:
            # The Codex backend routes prompt-cache *scope* by these request headers
            # (not by the body `prompt_cache_key`). A stable per-session id keeps the
            # cache warm across the many turns of one agent run — without it, requests
            # scatter across cache nodes and hit rate collapses to ~0.
            import hashlib as _hashlib
            scope = str(session_id or "").strip() or _hashlib.sha256(
                self._instructions(messages).encode("utf-8")).hexdigest()[:32]
            headers["session_id"] = scope
            headers["x-client-request-id"] = scope
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
        # The chatgpt.com/backend-api/codex backend rejects `metadata` and
        # `context_management` (400 Unsupported parameter); only the public
        # Responses API accepts them.
        if clean_metadata and not is_codex_backend:
            payload["metadata"] = clean_metadata
        context_management = self._context_management(
            state.get("context_management") or state.get("compaction")
        )
        if context_management and not is_codex_backend:
            payload["context_management"] = context_management
        if not is_codex_backend:
            payload["max_output_tokens"] = max_tokens
        clean_service_tier = str(service_tier or "").strip()
        if clean_service_tier and not is_xai:
            payload["service_tier"] = clean_service_tier
        eff = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
               "xhigh": "high"}.get(reasoning)
        if eff:
            # Request a reasoning summary so the model streams visible thinking
            # (`response.reasoning_summary_text.delta`) when a live display wants it.
            payload["reasoning"] = {"effort": eff, "summary": "auto"}
        wire_tools = self._to_wire_tools(tools)
        if wire_tools:
            payload["tools"] = wire_tools
        if response_format:                       # structured output -> Responses `text.format`
            payload["text"] = {"format": _to_text_format(response_format)}
        apply_request_overrides(payload, request_overrides, strip_service_tier=is_xai)
        if is_xai and session_id:
            headers["x-grok-conv-id"] = session_id
            cache_key = _content_cache_key(payload.get("instructions", ""), wire_tools) or session_id
            payload.setdefault("prompt_cache_key", cache_key)

        if wire_stream:
            resp = self._stream(url, headers, payload, on_delta, timeout, on_response_id, on_reasoning)
        else:
            resp = self._blocking(url, headers, payload, timeout)
            if on_response_id and isinstance(resp.raw, dict) and resp.raw.get("id"):
                on_response_id(str(resp.raw["id"]))
        self._remember_response(session_id, response_state or {}, model, resp, len(messages))
        return resp

    def _is_xai_responses(self, base_url: str, model: str) -> bool:
        text = f"{base_url} {model}".lower()
        return "api.x.ai" in text or "grok" in text or "xai" in text

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
        done_text_parts: list[str] = []
        output_items: list[dict[str, Any]] = []
        output_items_by_id: dict[str, dict[str, Any]] = {}
        output_items_by_index: dict[int, dict[str, Any]] = {}
        argument_buffers: dict[str, str] = {}
        completed: dict[str, Any] | None = None
        seen_response_ids: set[str] = set()

        def remember_output_item(item: Any, output_index: Any = None) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            item_id = str(item.get("id") or "")
            existing = output_items_by_id.get(item_id) if item_id else None
            if existing is None and output_index is not None:
                try:
                    existing = output_items_by_index.get(int(output_index))
                except (TypeError, ValueError):
                    existing = None
            if existing is None:
                existing = dict(item)
                output_items.append(existing)
            else:
                existing.update(item)
            if item_id:
                output_items_by_id[item_id] = existing
            if output_index is not None:
                try:
                    output_items_by_index[int(output_index)] = existing
                except (TypeError, ValueError):
                    pass
            return existing

        def stream_item_for(event: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
            item_id = str(event.get("item_id") or "")
            if item_id and item_id in output_items_by_id:
                return output_items_by_id[item_id], item_id
            output_index = event.get("output_index")
            try:
                index = int(output_index)
            except (TypeError, ValueError):
                index = -1
            if index >= 0 and index in output_items_by_index:
                return output_items_by_index[index], f"index:{index}"
            return None, item_id or (f"index:{index}" if index >= 0 else "")

        def merge_stream_items(response: dict[str, Any]) -> dict[str, Any]:
            if not output_items:
                return response
            current = response.get("output")
            if not isinstance(current, list) or not current:
                merged = dict(response)
                merged["output"] = output_items
                return merged
            seen = {
                (
                    str(item.get("id") or ""),
                    str(item.get("call_id") or ""),
                    str(item.get("type") or ""),
                )
                for item in current
                if isinstance(item, dict)
            }
            extras = [
                item for item in output_items
                if (
                    str(item.get("id") or ""),
                    str(item.get("call_id") or ""),
                    str(item.get("type") or ""),
                ) not in seen
            ]
            if not extras:
                return response
            merged = dict(response)
            merged["output"] = [*current, *extras]
            return merged

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
                    elif etype == "response.output_text.done":
                        text = event.get("text") or ""
                        if text:
                            done_text_parts.append(text)
                    elif etype == "response.output_item.added":
                        remember_output_item(event.get("item"), event.get("output_index"))
                    elif etype == "response.output_item.done":
                        remember_output_item(event.get("item"), event.get("output_index"))
                    elif etype == "response.function_call_arguments.delta":
                        delta = event.get("delta") or ""
                        item, key = stream_item_for(event)
                        if key and delta:
                            argument_buffers[key] = argument_buffers.get(key, "") + str(delta)
                            if item is not None:
                                item["arguments"] = argument_buffers[key]
                    elif etype == "response.function_call_arguments.done":
                        item, key = stream_item_for(event)
                        arguments = str(event.get("arguments") or (argument_buffers.get(key, "") if key else ""))
                        if key:
                            argument_buffers[key] = arguments
                        if item is not None:
                            item["arguments"] = arguments
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
            completed = merge_stream_items(completed)
            parsed = self._parse_response(completed)
            if parsed.text:
                return parsed
            parsed.text = "".join(text_parts)
            if not parsed.text:
                parsed.text = "".join(done_text_parts)
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
