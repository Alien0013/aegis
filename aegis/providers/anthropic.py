"""Anthropic Messages API transport (api.anthropic.com /v1/messages).

Supports both API key (x-api-key) and OAuth (Authorization: Bearer + the
``anthropic-beta: oauth-...`` header injected by the auth layer).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from .auth import AuthProvider
from .base import ApiMode, OnDelta, ProviderTransport
from .chat_completions import ProviderHTTPError, _raise_for_status

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicTransport(ProviderTransport):
    api_mode = ApiMode.ANTHROPIC_MESSAGES

    # -- wire conversion ----------------------------------------------------
    def _to_wire(self, messages: list[Message]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        out: list[dict] = []
        pending_user: list[dict] = []

        def flush_user():
            if pending_user:
                out.append({"role": "user", "content": list(pending_user)})
                pending_user.clear()

        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
            elif m.role == "user":
                if m.content:
                    pending_user.append({"type": "text", "text": m.content})
                for img in m.images:
                    # data URL -> base64 image block; http URL -> url source
                    if img.startswith("data:"):
                        meta, _, b64 = img.partition(",")
                        media = meta.split(";")[0].replace("data:", "") or "image/png"
                        pending_user.append(
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": media, "data": b64},
                            }
                        )
                    else:
                        pending_user.append(
                            {"type": "image", "source": {"type": "url", "url": img}}
                        )
            elif m.role == "assistant":
                flush_user()
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    )
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif m.role == "tool":
                pending_user.append(
                    {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                )
        flush_user()
        return "\n\n".join(system_parts), out

    def _to_wire_tools(self, tools: list[ToolSchema] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
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
    ) -> LLMResponse:
        url = f"{base_url}/v1/messages"
        system, wire_messages = self._to_wire(messages)
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            **(extra_headers or {}),
            **auth.headers(),
        }
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": wire_messages,
            "stream": stream,
        }
        # Extended thinking: map effort -> token budget (must be < max_tokens).
        budget = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384,
                  "xhigh": 32768}.get(reasoning)
        if budget:
            if max_tokens <= budget:
                payload["max_tokens"] = budget + 4096
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        if system:
            # Cache the (stable) system prompt prefix to cut cost/latency across turns.
            payload["system"] = [{"type": "text", "text": system,
                                  "cache_control": {"type": "ephemeral"}}]
        wire_tools = self._to_wire_tools(tools)
        if wire_tools:
            # Cache the tool definitions too (they're stable within a session).
            wire_tools[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = wire_tools

        if stream:
            return self._stream(url, headers, payload, on_delta, timeout)
        return self._blocking(url, headers, payload, timeout)

    def _blocking(self, url, headers, payload, timeout) -> LLMResponse:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
        _raise_for_status(r)
        data = r.json()
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(id=block["id"], name=block["name"], arguments=block.get("input", {}))
                )
        u = data.get("usage", {})
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=data.get("stop_reason"),
            usage=Usage(u.get("input_tokens", 0), u.get("output_tokens", 0)),
            raw=data,
        )

    def _stream(self, url, headers, payload, on_delta, timeout) -> LLMResponse:
        text_parts: list[str] = []
        blocks: dict[int, dict] = {}   # index -> {type, ...}
        tool_args: dict[int, str] = {}
        stop_reason = None
        usage = Usage()
        stream_timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)
        with httpx.Client(timeout=stream_timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as r:
                _raise_for_status(r)
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    etype = ev.get("type")
                    if etype == "content_block_start":
                        idx = ev["index"]
                        block = ev.get("content_block", {})
                        blocks[idx] = block
                        if block.get("type") == "tool_use":
                            tool_args[idx] = ""
                    elif etype == "content_block_delta":
                        idx = ev["index"]
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            t = delta.get("text", "")
                            text_parts.append(t)
                            if on_delta:
                                on_delta(t)
                        elif delta.get("type") == "input_json_delta":
                            tool_args[idx] = tool_args.get(idx, "") + delta.get("partial_json", "")
                    elif etype == "message_delta":
                        d = ev.get("delta", {})
                        if d.get("stop_reason"):
                            stop_reason = d["stop_reason"]
                        u = ev.get("usage", {})
                        if u:
                            usage.output_tokens = u.get("output_tokens", usage.output_tokens)
                    elif etype == "message_start":
                        u = ev.get("message", {}).get("usage", {})
                        usage.input_tokens = u.get("input_tokens", 0)
                    elif etype == "message_stop":
                        break
        tool_calls: list[ToolCall] = []
        for idx, block in sorted(blocks.items()):
            if block.get("type") == "tool_use":
                raw = tool_args.get(idx, "") or "{}"
                tool_calls.append(ToolCall.from_json_args(block["id"], block["name"], raw))
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=stop_reason,
            usage=usage,
        )


__all__ = ["AnthropicTransport", "ProviderHTTPError"]
