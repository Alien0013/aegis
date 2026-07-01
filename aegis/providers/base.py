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
OnResponseId = Callable[[str], None]
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
        metadata: dict | None = None,
        on_response_id: OnResponseId | None = None,
        service_tier: str = "",
        response_format: dict | None = None,
        request_overrides: dict | None = None,
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
        on_reasoning: OnDelta | None = None,
        session_id: str | None = None,
        response_state: dict | None = None,
        metadata: dict | None = None,
        on_response_id: OnResponseId | None = None,
        service_tier: str = "",
        response_format: dict | None = None,
    ) -> LLMResponse:
        # Live thinking stream: only transports that accept on_reasoning get it.
        extra_kwargs = {}
        import inspect
        try:
            params = inspect.signature(self.transport.complete).parameters
        except (TypeError, ValueError):
            params = {}
        if on_reasoning is not None and "on_reasoning" in params:
            extra_kwargs["on_reasoning"] = on_reasoning
        if session_id is not None and "session_id" in params:
            extra_kwargs["session_id"] = session_id
        if response_state is not None and "response_state" in params:
            extra_kwargs["response_state"] = response_state
        if metadata is not None and "metadata" in params:
            extra_kwargs["metadata"] = metadata
        if on_response_id is not None and "on_response_id" in params:
            extra_kwargs["on_response_id"] = on_response_id
        clean_service_tier = str(service_tier or "").strip()
        if clean_service_tier and "service_tier" in params:
            extra_kwargs["service_tier"] = clean_service_tier
        if response_format and "response_format" in params:
            extra_kwargs["response_format"] = response_format
        request_overrides = getattr(self, "request_overrides", None)
        if isinstance(request_overrides, dict) and "request_overrides" in params:
            extra_kwargs["request_overrides"] = request_overrides
        fallback_orchestrated = bool(
            isinstance(metadata, dict) and metadata.get("_fallback_orchestrated")
        )
        if fallback_orchestrated and isinstance(metadata, dict):
            cleaned_metadata = dict(metadata)
            cleaned_metadata.pop("_fallback_orchestrated", None)
            if "metadata" in params:
                extra_kwargs["metadata"] = cleaned_metadata
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
                    **extra_kwargs,
                )
            except Exception as e:  # noqa: BLE001
                # Let the classifier decide whether this can recover on the same
                # provider. When FallbackProvider is orchestrating, non-credential
                # recovery is handed back immediately so the chain can advance.
                import random
                import time
                from .fallback import classify_provider_failure, provider_failure_error_context
                failure = classify_provider_failure(
                    e,
                    provider=self,
                    model=model or self.model,
                    base_url=self.base_url,
                )
                kind = failure.legacy_reason
                action = failure.recovery
                error_context = provider_failure_error_context(e, failure)
                credential_rotated = False
                if failure.rotate_credentials:
                    report_kind = "auth_permanent" if failure.reason == "auth_permanent" else kind
                    report = getattr(self.auth, "report", None)
                    if callable(report):
                        try:
                            credential_rotated = bool(report(report_kind, error_context=error_context))
                        except TypeError:
                            credential_rotated = bool(report(report_kind))
                    elif hasattr(self.auth, "rotate"):
                        credential_rotated = bool(self.auth.rotate())
                if action in ("abort", "compress", "strip_thinking"):
                    raise
                if action == "rotate" and not credential_rotated:
                    raise
                if fallback_orchestrated and failure.fallback_eligible and not (
                    credential_rotated and failure.retry_same_provider
                ):
                    raise
                if attempts >= 4:
                    raise
                if credential_rotated and failure.retry_same_provider:
                    attempts += 1
                    continue
                time.sleep(min(30.0, (2 ** attempts) * 1.5) + random.random())
                attempts += 1
                continue

    def cancel_response(self, response_id: str) -> dict | None:
        cancel = getattr(self.transport, "cancel_response", None)
        if not callable(cancel) or not response_id:
            return None
        return cancel(
            base_url=self.base_url,
            auth=self.auth,
            response_id=response_id,
            extra_headers=self.extra_headers,
        )

    def describe(self) -> str:
        return f"{self.name} · {self.model} · {self.api_mode.value} · {self.auth.describe()}"
