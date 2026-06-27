"""Process-level bootstrap helpers for AEGIS agent runtimes.

This module keeps low-level process setup separate from the main agent loop:

- safe stdout/stderr wrappers for services, containers, and background workers
- environment proxy discovery with NO_PROXY bypass checks

The helpers are intentionally small and side-effect free until called.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from typing import Optional
from urllib.parse import urlparse


class _SafeWriter:
    """Transparent stream wrapper that ignores broken-pipe style write errors."""

    __slots__ = ("_inner",)

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def write(self, data):
        try:
            return self._inner.write(data)
        except (OSError, ValueError):
            return len(data) if isinstance(data, str) else 0

    def flush(self):
        try:
            self._inner.flush()
        except (OSError, ValueError):
            return None
        return None

    def fileno(self):
        return self._inner.fileno()

    def isatty(self):
        try:
            return self._inner.isatty()
        except (OSError, ValueError):
            return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _install_safe_stdio() -> None:
    """Wrap stdout/stderr so best-effort logging cannot crash the process."""

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _SafeWriter):
            setattr(sys, stream_name, _SafeWriter(stream))


def _normalize_proxy_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        return raw
    return f"http://{raw}"


def _base_url_hostname(base_url: Optional[str]) -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").strip()


def _get_proxy_from_env() -> Optional[str]:
    """Return the highest-priority proxy URL from standard proxy env vars."""

    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return _normalize_proxy_url(value)
    return None


def _get_proxy_for_base_url(base_url: Optional[str]) -> Optional[str]:
    """Return the configured proxy unless NO_PROXY excludes the target host."""

    proxy = _get_proxy_from_env()
    if not proxy:
        return None
    host = _base_url_hostname(base_url)
    if not host:
        return proxy
    try:
        bypass = getattr(urllib.request, "proxy_bypass_environment", None)
        if callable(bypass) and bypass(host):
            return None
    except Exception:  # noqa: BLE001
        return proxy
    return proxy


__all__ = [
    "_SafeWriter",
    "_install_safe_stdio",
    "_get_proxy_from_env",
    "_get_proxy_for_base_url",
]
