"""Pluggable context-management strategy.

The agent loop asks a ``ContextEngine`` whether the window is full and how to shrink it. The
default wraps the built-in summarize-compaction; a plugin can register an alternative strategy
(e.g. a hierarchical/DAG compressor) and even expose its own tools (e.g. a tool to query the
compacted history). Select via ``agent.context_engine`` in config.
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContextEngine(Protocol):
    name: str

    def should_compress(
        self,
        messages: list,
        context_length: int,
        overhead_tokens: int = 0,
        max_output_tokens: int | None = None,
    ) -> bool:
        ...

    def compress(self, messages: list, provider: Any, **kw) -> list:
        ...

    def tools(self) -> list:
        """Optional tools this engine exposes to the agent (e.g. query compacted history)."""
        ...

    def on_session_start(self, agent: Any) -> None:
        ...

    def on_pre_compress(self, agent: Any, session: Any) -> None:
        ...

    def on_session_switch(self, agent: Any, old_session: Any, new_session: Any, reason: str = "") -> None:
        ...


class DefaultContextEngine:
    """Wraps the built-in summarization compaction (aegis.agent.compaction)."""

    name = "default"

    def __init__(self, threshold: float | None = None):
        # Fraction of the window that triggers compaction (None = built-in default).
        self._threshold = threshold

    def should_compress(
        self,
        messages: list,
        context_length: int,
        overhead_tokens: int = 0,
        max_output_tokens: int | None = None,
    ) -> bool:
        from . import compaction
        try:
            return compaction.should_compress(
                messages,
                context_length,
                overhead_tokens,
                self._threshold,
                max_output_tokens=max_output_tokens,
            )
        except TypeError:
            return compaction.should_compress(messages, context_length, overhead_tokens, self._threshold)

    def threshold_fraction(self) -> float | None:
        return self._threshold

    def set_threshold_fraction(self, threshold: float | None) -> None:
        self._threshold = threshold

    def compress(self, messages: list, provider: Any, **kw) -> list:
        from . import compaction
        return compaction.compress(messages, provider, **kw)

    def tools(self) -> list:
        return []

    def on_session_start(self, agent: Any) -> None:
        return None

    def on_pre_compress(self, agent: Any, session: Any) -> None:
        return None

    def on_session_switch(self, agent: Any, old_session: Any, new_session: Any, reason: str = "") -> None:
        return None


_ENGINES: dict[str, type] = {"default": DefaultContextEngine}


def register(name: str, engine_cls: type) -> None:
    """Register a context-engine implementation under ``name`` (used by plugins)."""
    _ENGINES[name] = engine_cls


def get_engine(config) -> "ContextEngine":
    """Resolve the configured context engine (falls back to the default)."""
    name = (config.get("agent.context_engine", "default") if config else "default") or "default"
    cls = _ENGINES.get(name, DefaultContextEngine)
    if cls is DefaultContextEngine:
        threshold = config.get("agent.compression.threshold") if config else None
        return DefaultContextEngine(threshold=threshold)
    return cls()


def call_hook(engine: Any, name: str, *args: Any, **kwargs: Any) -> None:
    """Best-effort optional lifecycle hook call for context engines."""

    fn = getattr(engine, name, None)
    if not callable(fn):
        return
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        fn(*args, **kwargs)
        return
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        fn(*args, **kwargs)
        return
    allowed = {k: v for k, v in kwargs.items() if k in params}
    fn(*args[:len(params)], **allowed)
