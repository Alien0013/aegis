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
from .schema import sanitize as _sanitize_schema

ANTHROPIC_VERSION = "2023-06-01"

_CACHE_TTL: str | None = None


def _cache_ttl() -> str:
    """Configured prompt-cache TTL ("5m" default | "1h"), read once per process."""
    global _CACHE_TTL
    if _CACHE_TTL is None:
        try:
            from ..config import Config
            _CACHE_TTL = (Config.load().get("prompt_caching.cache_ttl", "5m") or "5m")
        except Exception:  # noqa: BLE001
            _CACHE_TTL = "5m"
    return _CACHE_TTL


def _cache_marker() -> dict[str, Any]:
    """Anthropic cache_control marker honoring the configured TTL (1h needs the field; 5m is default)."""
    ttl = _cache_ttl()
    return {"type": "ephemeral", "ttl": ttl} if ttl and ttl != "5m" else {"type": "ephemeral"}

# Models on the adaptive-thinking API surface (budget_tokens would 400 on fable/4.7/4.8
# and is deprecated on the 4.6 family). Prefix-matched.
_ADAPTIVE_THINKING_PREFIXES = ("claude-fable", "claude-opus-4-6", "claude-opus-4-7",
                               "claude-opus-4-8", "claude-sonnet-4-6")


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
                # Thinking blocks (with signatures) MUST precede text/tool_use and be
                # echoed verbatim, or thinking+tool-use turns are rejected by the API.
                blocks.extend(getattr(m, "thinking_blocks", []) or [])
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
                "input_schema": _sanitize_schema(
                    t.get("parameters", {"type": "object", "properties": {}})),
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
    ) -> LLMResponse:
        url = f"{base_url}/v1/messages"
        system, wire_messages = self._to_wire(messages)
        # Conversation cache breakpoints ("system + last 3"): mark the final content block
        # of the last 3 wire messages so the whole conversation prefix is a cache READ on
        # the next turn instead of being re-billed at full input price every call
        # (~75% input-cost cut on multi-turn sessions; markers move forward each turn).
        for wm in wire_messages[-3:]:
            blocks = wm.get("content")
            if isinstance(blocks, list) and blocks and isinstance(blocks[-1], dict):
                blocks[-1]["cache_control"] = _cache_marker()
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
        # Thinking. Fable 5 / Opus 4.6+ / Sonnet 4.6 use ADAPTIVE thinking + the effort
        # parameter (budget_tokens is removed there and returns a 400); older models keep
        # the legacy budget_tokens mode. reasoning == "off" sends no thinking field at all.
        if reasoning != "off" and reasoning in ("minimal", "low", "medium", "high", "xhigh"):
            if model.startswith(_ADAPTIVE_THINKING_PREFIXES):
                payload["thinking"] = {"type": "adaptive", "display": "summarized"}
                payload["output_config"] = {"effort": {
                    "minimal": "low", "low": "low", "medium": "medium",
                    "high": "high", "xhigh": "max"}[reasoning]}
            else:
                budget = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384,
                          "xhigh": 32768}[reasoning]
                if max_tokens <= budget:
                    payload["max_tokens"] = budget + 4096
                payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        # claude.ai/Claude-Code OAuth tokens require the system prompt to begin with the
        # Claude Code identity block, or the Messages API rejects the request. Detect OAuth
        # by the bearer beta header and prepend it.
        is_oauth = "oauth" in headers.get("anthropic-beta", "")
        sys_blocks: list[dict[str, Any]] = []
        if is_oauth:
            sys_blocks.append({"type": "text",
                               "text": "You are Claude Code, Anthropic's official CLI for Claude."})
        if system:
            # Cache the (stable) system prompt prefix to cut cost/latency across turns.
            sys_blocks.append({"type": "text", "text": system,
                               "cache_control": _cache_marker()})
        if sys_blocks:
            payload["system"] = sys_blocks
        wire_tools = self._to_wire_tools(tools)
        if wire_tools:
            # No marker on tools: the system-prompt breakpoint already caches the tools
            # prefix, and Anthropic allows at most 4 breakpoints (1 system + 3 messages).
            payload["tools"] = wire_tools

        if stream:
            return self._stream(url, headers, payload, on_delta, timeout, on_reasoning)
        return self._blocking(url, headers, payload, timeout)

    def _blocking(self, url, headers, payload, timeout) -> LLMResponse:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=headers, json=payload)
        _raise_for_status(r)
        try:
            from .. import ratelimit
            ratelimit.record(r.headers, "anthropic")
        except Exception:  # noqa: BLE001
            pass
        data = r.json()
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        reasoning_parts: list[str] = []
        thinking_blocks: list[dict] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(id=block["id"], name=block["name"], arguments=block.get("input", {}))
                )
            elif block.get("type") in ("thinking", "redacted_thinking"):
                thinking_blocks.append(block)            # verbatim, incl. signature
                if block.get("thinking"):
                    reasoning_parts.append(block["thinking"])
        u = data.get("usage", {})
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=data.get("stop_reason"),
            reasoning="\n".join(reasoning_parts),
            thinking_blocks=thinking_blocks,
            usage=Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                        u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0)),
            raw=data,
        )

    def _stream(self, url, headers, payload, on_delta, timeout,
                on_reasoning=None) -> LLMResponse:
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
                        elif delta.get("type") == "thinking_delta":
                            t = delta.get("thinking", "")
                            b = blocks.get(idx)
                            if b is not None:
                                b["thinking"] = b.get("thinking", "") + t
                            if on_reasoning and t:
                                on_reasoning(t)
                        elif delta.get("type") == "signature_delta":
                            b = blocks.get(idx)
                            if b is not None:
                                b["signature"] = b.get("signature", "") + delta.get("signature", "")
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
                        usage.cache_read = u.get("cache_read_input_tokens", 0)
                        usage.cache_write = u.get("cache_creation_input_tokens", 0)
                    elif etype == "message_stop":
                        break
        tool_calls: list[ToolCall] = []
        thinking_blocks: list[dict] = []
        reasoning_parts: list[str] = []
        for idx, block in sorted(blocks.items()):
            if block.get("type") == "tool_use":
                raw = tool_args.get(idx, "") or "{}"
                tool_calls.append(ToolCall.from_json_args(block["id"], block["name"], raw))
            elif block.get("type") in ("thinking", "redacted_thinking"):
                thinking_blocks.append(block)            # rebuilt verbatim (text + signature)
                if block.get("thinking"):
                    reasoning_parts.append(block["thinking"])
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=stop_reason,
            reasoning="\n".join(reasoning_parts),
            thinking_blocks=thinking_blocks,
            usage=usage,
        )


__all__ = ["AnthropicTransport", "ProviderHTTPError"]
