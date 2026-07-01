"""Pluggable context-management strategy.

The agent loop asks a ``ContextEngine`` whether the window is full and how to shrink it. The
default wraps the built-in summarize-compaction; a plugin can register an alternative strategy
(e.g. a hierarchical/DAG compressor) and even expose its own tools (e.g. a tool to query the
compacted history). Select via ``agent.context_engine`` in config.
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


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


_ENGINES: dict[str, Any] = {"default": DefaultContextEngine, "compressor": DefaultContextEngine}


def register(name: str, engine_cls: Any) -> None:
    """Register a context-engine implementation under ``name`` (used by plugins)."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("context engine name is required")
    _ENGINES[clean] = engine_cls


def _config_value(config, dotted: str, default: Any = None) -> Any:
    if config is None:
        return default
    get = getattr(config, "get", None)
    if callable(get):
        try:
            return get(dotted, default)
        except TypeError:
            try:
                value = get(dotted)
            except Exception:  # noqa: BLE001
                return default
            return default if value is None else value
    return default


def _selected_engine_name(config) -> str:
    agent_name = _config_value(config, "agent.context_engine", "default")
    context_name = _config_value(config, "context.engine", None)
    if context_name and str(agent_name or "").strip().lower() in {"", "default", "compressor"}:
        name = context_name
    else:
        name = agent_name
    clean = str(name or "default").strip()
    return "default" if clean in {"", "compressor"} else clean


def _default_engine(config) -> DefaultContextEngine:
    threshold = _config_value(config, "agent.compression.threshold", None)
    return DefaultContextEngine(threshold=threshold)


def _fresh_registered_engine(name: str, entry: Any, config) -> ContextEngine | None:
    if entry is DefaultContextEngine or (isinstance(entry, str) and entry in {"default", "compressor"}):
        return _default_engine(config)
    if isinstance(entry, type):
        return entry()
    if callable(entry) and not isinstance(entry, ContextEngine):
        try:
            return entry()
        except TypeError:
            return None
    try:
        return copy.deepcopy(entry)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Context engine '%s' could not be safely copied (%s); falling back to default",
            name,
            exc,
        )
        return None


def _context_engine_roots(config=None) -> list[Path]:
    roots: list[Path] = []
    for raw in _config_value(config, "context.engine_paths", []) or []:
        try:
            roots.append(Path(raw).expanduser())
        except TypeError:
            continue
    try:
        from .. import config as cfg

        roots.append(cfg.sub("plugins") / "context_engine")
    except Exception:  # noqa: BLE001
        pass
    try:
        project_root = Path.cwd() / ".aegis" / "plugins" / "context_engine"
        roots.append(project_root)
    except Exception:  # noqa: BLE001
        pass
    return roots


def _load_engine_from_dir(engine_dir: Path) -> ContextEngine | None:
    init_file = engine_dir / "__init__.py"
    if not init_file.is_file():
        return None
    module_name = f"aegis_context_engine_{engine_dir.name}_{abs(hash(str(engine_dir.resolve())))}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(init_file),
        submodule_search_locations=[str(engine_dir)],
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(module_name, None)
        logger.warning("Failed to load context engine '%s': %s", engine_dir.name, exc)
        return None

    register_fn = getattr(module, "register", None)
    if callable(register_fn):
        collector = _EngineCollector(engine_dir.name)
        try:
            register_fn(collector)
            if collector.engine is not None:
                return collector.engine
        except Exception as exc:  # noqa: BLE001
            logger.debug("Context engine register() failed for %s: %s", engine_dir.name, exc)

    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if isinstance(attr, type) and attr is not ContextEngine:
            try:
                instance = attr()
            except Exception:
                continue
            if hasattr(instance, "should_compress") and hasattr(instance, "compress"):
                return instance
    return None


def load_context_engine(name: str, config=None) -> ContextEngine | None:
    """Load an engine from configured/user context-engine plugin directories."""
    clean = str(name or "").strip()
    if not clean:
        return None
    for root in _context_engine_roots(config):
        engine_dir = root / clean
        if not engine_dir.is_dir():
            continue
        engine = _load_engine_from_dir(engine_dir)
        if engine is not None:
            return engine
    return None


class _EngineCollector:
    def __init__(self, engine_name: str):
        self.engine = None
        self.engine_name = engine_name

    def register_context_engine(self, *args) -> None:
        if len(args) == 1:
            self.engine = args[0]
        elif len(args) >= 2:
            name = str(args[0])
            register(name, args[1])
            if name == self.engine_name:
                self.engine = _fresh_registered_engine(name, _ENGINES.get(name), None)

    def register_tool(self, *_args, **_kwargs) -> None:
        return None

    def register_hook(self, *_args, **_kwargs) -> None:
        return None


def get_engine(config) -> "ContextEngine":
    """Resolve the configured context engine (falls back to the default)."""
    name = _selected_engine_name(config)
    entry = _ENGINES.get(name)
    if entry is not None:
        engine = _fresh_registered_engine(name, entry, config)
        if engine is not None:
            return engine
    plugin_engine = load_context_engine(name, config=config)
    if plugin_engine is not None:
        try:
            return copy.deepcopy(plugin_engine)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Context engine '%s' could not be safely copied (%s); falling back to default",
                name,
                exc,
            )
    if name not in {"default", "compressor"}:
        logger.warning("Context engine '%s' not found; falling back to default", name)
    return _default_engine(config)


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
