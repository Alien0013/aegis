"""Provider transport ABC and the bound Provider object the agent loop uses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Callable

from ..tools.base import ToolResult
from ..types import LLMResponse, Message, ToolCall, ToolSchema
from .auth import AuthProvider

OnDelta = Callable[[str], None]
ToolRunner = Callable[[ToolCall], ToolResult]
ApprovalHandler = Callable[[str], bool]


class ApiMode(str, Enum):
    """Wire protocol family. New protocol = new transport, never if/elif sprawl."""

    CHAT_COMPLETIONS = "chat_completions"   # OpenAI-compatible
    ANTHROPIC_MESSAGES = "anthropic_messages"
    RESPONSES = "responses"                 # OpenAI Responses API / Codex backend
    CODEX_APP_SERVER = "codex_app_server"   # Local Codex CLI app-server runtime


class ProviderTransport(ABC):
    """One implementation per wire protocol. Owns message+tool conversion."""

    api_mode: ApiMode

    @abstractmethod
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
        tool_runner: ToolRunner | None = None,
        approver: ApprovalHandler | None = None,
        cwd: Path | None = None,
    ) -> LLMResponse:
        """Make one completion call and return a normalized response."""
        raise NotImplementedError


class Provider:
    """A transport bound to a concrete endpoint, model, and auth strategy."""

    def __init__(
        self,
        *,
        name: str,
        transport: ProviderTransport,
        auth: AuthProvider,
        base_url: str,
        model: str,
        context_length: int,
        api_mode: ApiMode,
        max_tokens: int = 8192,
        extra_headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.transport = transport
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_length = context_length
        self.api_mode = api_mode
        self.max_tokens = max_tokens
        self.extra_headers = extra_headers or {}

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        stream: bool = False,
        on_delta: OnDelta | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        reasoning: str = "off",
        tool_runner: ToolRunner | None = None,
        approver: ApprovalHandler | None = None,
        cwd: Path | None = None,
    ) -> LLMResponse:
        attempts = 0
        while True:
            try:
                return self.transport.complete(
                    base_url=self.base_url,
                    auth=self.auth,
                    model=model or self.model,
                    messages=messages,
                    tools=tools,
                    stream=stream,
                    on_delta=on_delta,
                    max_tokens=max_tokens or self.max_tokens,
                    extra_headers=self.extra_headers,
                    reasoning=reasoning,
                    tool_runner=tool_runner,
                    approver=approver,
                    cwd=cwd,
                )
            except Exception as e:  # noqa: BLE001
                # Classify: retry transient errors (rate-limit, 5xx, timeouts, dropped
                # streams) with jittered exponential backoff; rotate keys on 401/429.
                import random
                import time
                status = getattr(e, "status", None)
                transient_status = status in (408, 409, 425, 429, 500, 502, 503, 504, 529)
                transient_net = type(e).__name__ in (
                    "TimeoutException", "ConnectError", "ConnectTimeout", "ReadTimeout",
                    "ReadError", "RemoteProtocolError", "PoolTimeout")
                if attempts < 4 and (transient_status or transient_net):
                    if status in (401, 429) and hasattr(self.auth, "rotate"):
                        self.auth.rotate()
                    time.sleep(min(30.0, (2 ** attempts) * 1.5) + random.random())
                    attempts += 1
                    continue
                raise

    def describe(self) -> str:
        return f"{self.name} · {self.model} · {self.api_mode.value} · {self.auth.describe()}"
