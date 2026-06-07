"""Provider transport ABC and the bound Provider object the agent loop uses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable

from ..types import LLMResponse, Message, ToolSchema
from .auth import AuthProvider

OnDelta = Callable[[str], None]


class ApiMode(str, Enum):
    """Wire protocol family. New protocol = new transport, never if/elif sprawl."""

    CHAT_COMPLETIONS = "chat_completions"   # OpenAI-compatible
    ANTHROPIC_MESSAGES = "anthropic_messages"
    RESPONSES = "responses"                 # OpenAI Responses API (future)


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
                )
            except Exception as e:  # noqa: BLE001
                # On rate-limit / auth errors, rotate the credential pool and retry.
                status = getattr(e, "status", None)
                if (status in (401, 429, 529) and attempts < 5
                        and hasattr(self.auth, "rotate") and self.auth.rotate()):
                    attempts += 1
                    continue
                raise

    def describe(self) -> str:
        return f"{self.name} · {self.model} · {self.api_mode.value} · {self.auth.describe()}"
