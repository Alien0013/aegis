"""OpenAI-compatible Chat Completions transport.

Covers OpenAI, OpenRouter, Groq, DeepSeek, xAI, Mistral, Together, Ollama,
LM Studio, vLLM, and Google Gemini (via its OpenAI-compatible endpoint).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..types import LLMResponse, Message, ToolCall, ToolSchema, Usage
from .schema import sanitize as _sanitize_schema
from .base import ApiMode, OnDelta, ProviderTransport
from .auth import AuthProvider


def _reported_cost(usage: dict) -> float | None:
    """Provider-reported actual USD cost when the API returns one (e.g. OpenRouter's
    ``usage.cost``). Returns None when absent so the caller falls back to an estimate."""
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if cost is None:
        details = usage.get("cost_details")
        if isinstance(details, dict):
            cost = details.get("upstream_inference_cost") or details.get("total_cost")
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


class ChatCompletionsTransport(ProviderTransport):
    api_mode = ApiMode.CHAT_COMPLETIONS

    # -- wire conversion ----------------------------------------------------
    def _to_wire_messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
                continue
            if m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
                continue
            if m.role == "user" and m.images:
                parts: list[dict] = [{"type": "text", "text": m.content}]
                for img in m.images:
                    parts.append({"type": "image_url", "image_url": {"url": img}})
                out.append({"role": "user", "content": parts})
                continue
            out.append({"role": m.role, "content": m.content})
        return out

    def _to_wire_tools(self, tools: list[ToolSchema] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": _sanitize_schema(
                        t.get("parameters", {"type": "object", "properties": {}})),
                },
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
        on_reasoning: OnDelta | None = None,
        service_tier: str = "",
    ) -> LLMResponse:
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json", **(extra_headers or {}), **auth.headers()}
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_wire_messages(messages),
            "stream": stream,
        }
        # Reasoning effort (OpenAI o-series + compatible): low | medium | high.
        eff = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
               "xhigh": "high"}.get(reasoning)
        if eff:
            payload["reasoning_effort"] = eff
        clean_service_tier = str(service_tier or "").strip()
        if clean_service_tier and not self._is_xai_chat(base_url, model):
            payload["service_tier"] = clean_service_tier
        wire_tools = self._to_wire_tools(tools)
        if wire_tools:
            payload["tools"] = wire_tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream_options"] = {"include_usage": True}

        if stream:
            return self._stream(url, headers, payload, on_delta, timeout, on_reasoning)
        return self._blocking(url, headers, payload, timeout)

    def _is_xai_chat(self, base_url: str, model: str) -> bool:
        text = f"{base_url} {model}".lower()
        return "api.x.ai" in text or "grok" in text or "xai" in text

    def _blocking(self, url, headers, payload, timeout) -> LLMResponse:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
        _raise_for_status(r)
        from .. import ratelimit
        ratelimit.record_response_headers(getattr(r, "headers", {}), base_url=url)
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        tool_calls = [
            ToolCall.from_json_args(
                tc.get("id") or f"call_{i}",
                tc["function"]["name"],
                tc["function"].get("arguments", "") or "",
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        usage = data.get("usage") or {}
        usage_obj = Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                          (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        usage_obj.cost = _reported_cost(usage)
        return LLMResponse(
            text=msg.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
            usage=usage_obj,
            raw=data,
        )

    def _stream(self, url, headers, payload, on_delta, timeout,
                on_reasoning=None) -> LLMResponse:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        # tool calls accumulate by index
        tc_acc: dict[int, dict] = {}
        finish_reason = None
        usage = Usage()
        # 90s read timeout = abort a stalled/stale stream instead of hanging.
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
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                                      (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
                        usage.cost = _reported_cost(u)
                    for choice in chunk.get("choices", []):
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if content:
                            text_parts.append(content)
                            if on_delta:
                                on_delta(content)
                        rc = delta.get("reasoning_content") or delta.get("reasoning")
                        if rc:                       # DeepSeek/OpenRouter-style reasoning stream
                            reasoning_parts.append(rc)
                            if on_reasoning:
                                on_reasoning(rc)
                        for tc in delta.get("tool_calls", []):
                            idx = tc.get("index", 0)
                            slot = tc_acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
        tool_calls = [
            ToolCall.from_json_args(slot["id"] or f"call_{idx}", slot["name"], slot["args"])
            for idx, slot in sorted(tc_acc.items())
            if slot["name"]
        ]
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            reasoning="".join(reasoning_parts),
            usage=usage,
        )


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    body = ""
    try:
        body = r.read().decode("utf-8", "replace") if hasattr(r, "read") else r.text
    except Exception:
        body = getattr(r, "text", "")
    raise ProviderHTTPError(r.status_code, body[:500])


class ProviderHTTPError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")
