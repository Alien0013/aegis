"""Process-shared helpers for bounded background MCP discovery."""

from __future__ import annotations

import logging
import threading
from typing import Any

_lock = threading.Lock()
_started = False
_thread: threading.Thread | None = None
_result: tuple[list[Any], Any] | None = None
_error: BaseException | None = None


def reset_background_mcp_discovery_for_tests() -> None:
    global _started, _thread, _result, _error
    with _lock:
        _started = False
        _thread = None
        _result = None
        _error = None


def _has_configured_mcp_servers(config: Any) -> bool:
    try:
        if not config.get("mcp.enabled", True):
            return False
        servers = config.get("mcp.servers", {}) or {}
    except Exception:  # noqa: BLE001
        return True
    if not isinstance(servers, dict):
        return False
    for spec in servers.values():
        if isinstance(spec, dict) and (spec.get("command") or spec.get("url")):
            return True
    return False


def _resolve_discovery_timeout(config: Any | None, explicit: float | None) -> float:
    if explicit is not None:
        return max(float(explicit), 0.0)
    try:
        value = float(config.get("mcp.discovery_timeout", 1.5)) if config is not None else 1.5
    except Exception:  # noqa: BLE001
        return 1.5
    return value if value > 0 else 1.5


def _discover_mcp_tools(config: Any) -> tuple[list[Any], Any]:
    from .client import MCPManager, build_manager

    if not config.get("mcp.enabled", True):
        return [], MCPManager()
    manager = build_manager(config)
    return manager.connect_all(), manager


def start_background_mcp_discovery(
    config: Any,
    *,
    logger: logging.Logger | None = None,
    thread_name: str = "aegis-mcp-discovery",
) -> None:
    """Start one process-wide MCP discovery thread when MCP servers exist."""
    global _started, _thread, _result, _error
    with _lock:
        if _started:
            return
        _started = True
        if not _has_configured_mcp_servers(config):
            return

        def run() -> None:
            global _result, _error
            try:
                discovered = _discover_mcp_tools(config)
            except BaseException as exc:  # noqa: BLE001
                _error = exc
                log = logger or logging.getLogger(__name__)
                log.debug("Background MCP tool discovery failed", exc_info=True)
            else:
                _result = discovered

        thread = threading.Thread(target=run, name=thread_name, daemon=True)
        _thread = thread
        thread.start()


def mcp_discovery_in_flight() -> bool:
    thread = _thread
    return thread is not None and thread.is_alive()


def wait_for_mcp_discovery(config: Any | None = None, timeout: float | None = None) -> None:
    thread = _thread
    if thread is None or not thread.is_alive():
        return
    thread.join(timeout=_resolve_discovery_timeout(config, timeout))


def join_mcp_discovery(timeout: float | None = None) -> bool:
    thread = _thread
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


def background_mcp_discovery_error() -> BaseException | None:
    return _error


def claim_background_mcp_discovery(
    config: Any | None = None,
    *,
    timeout: float | None = None,
) -> tuple[list[Any], Any] | None:
    """Return the completed background discovery result once, if available."""
    global _result
    wait_for_mcp_discovery(config, timeout)
    with _lock:
        thread = _thread
        if thread is not None and thread.is_alive():
            return None
        if _result is None:
            return None
        result = _result
        _result = None
        return result
