"""Manage live Chromium-family CDP browser connections."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlparse
from typing import Any

from . import config as cfg

DEFAULT_BROWSER_CDP_PORT = 9222
DEFAULT_BROWSER_CDP_URL = f"http://127.0.0.1:{DEFAULT_BROWSER_CDP_PORT}"

_DARWIN_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_LINUX_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium-browser",
    "chromium",
    "brave-browser",
    "brave-browser-stable",
    "brave",
    "microsoft-edge",
    "microsoft-edge-stable",
    "msedge",
)
_LINUX_PATHS = (
    "/opt/google/chrome/chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/brave-browser",
    "/usr/bin/brave-browser-stable",
    "/usr/bin/brave",
    "/snap/bin/brave",
    "/opt/brave.com/brave/brave-browser",
    "/opt/brave.com/brave/brave",
    "/usr/bin/microsoft-edge",
    "/usr/bin/microsoft-edge-stable",
    "/opt/microsoft/msedge/microsoft-edge",
)
_WINDOWS_NAMES = ("chrome.exe", "chrome", "chromium.exe", "chromium", "brave.exe", "brave", "msedge.exe", "msedge")
_WINDOWS_INSTALLS = (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Chromium", "Application", "chrome.exe"),
    ("Chromium", "Application", "chromium.exe"),
    ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"),
)


def normalize_cdp_url(raw: Any = "") -> tuple[str, int]:
    """Normalize user CDP input and return ``(url, port)``.

    Keeps concrete ``/devtools/browser/...`` websocket endpoints intact. For
    discovery URLs such as ``host:9222`` or ``http://host:9222/json/version``,
    returns the root endpoint that Chromium exposes.
    """
    value = str(raw or DEFAULT_BROWSER_CDP_URL).strip()
    parsed = urlparse(value if "://" in value else f"http://{value}")
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError(
            f"unsupported browser url scheme: {parsed.scheme or '(missing)'} "
            "(expected one of: http, https, ws, wss)"
        )
    try:
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    except ValueError as exc:
        raise ValueError(f"invalid port in browser url: {value}") from exc
    if not parsed.hostname:
        raise ValueError(f"missing host in browser url: {value}")
    if parsed.path.startswith("/devtools/browser/"):
        return parsed.geturl(), port
    return parsed._replace(path="", params="", query="", fragment="").geturl(), port


def playwright_cdp_endpoint(raw: Any = "") -> str:
    url, _port = normalize_cdp_url(raw)
    parsed = urlparse(url)
    if parsed.scheme == "ws" and not parsed.path:
        return parsed._replace(scheme="http").geturl()
    if parsed.scheme == "wss" and not parsed.path:
        return parsed._replace(scheme="https").geturl()
    return url


def current_cdp_url(config: Any = None, *, for_playwright: bool = False) -> str:
    raw = os.environ.get("BROWSER_CDP_URL", "").strip()
    if not raw and config is not None:
        raw = str(config.get("browser.cdp_url", "") or "").strip()
    if not raw:
        return ""
    return playwright_cdp_endpoint(raw) if for_playwright else raw


def is_browser_debug_ready(url: str, timeout: float = 1.0) -> bool:
    try:
        normalized, port = normalize_cdp_url(url)
    except ValueError:
        return False
    parsed = urlparse(normalized)
    if parsed.scheme in {"ws", "wss"} and parsed.path.startswith("/devtools/browser/"):
        if not parsed.hostname:
            return False
        try:
            with socket.create_connection((parsed.hostname, port), timeout=timeout):
                return True
        except OSError:
            return False

    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"http", "https"} or not parsed.netloc:
        return False
    root = f"{scheme}://{parsed.netloc}".rstrip("/")
    for probe in (f"{root}/json/version", f"{root}/json"):
        try:
            with urllib.request.urlopen(probe, timeout=timeout) as resp:
                if 200 <= getattr(resp, "status", 200) < 300:
                    return True
        except Exception:
            continue
    return False


def chrome_debug_data_dir() -> str:
    return str(cfg.get_home() / "chrome-debug")


def get_chrome_debug_candidates(system: str | None = None) -> list[str]:
    system = system or platform.system()
    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if not path:
            return
        normalized = os.path.normcase(os.path.normpath(path))
        if normalized in seen or not os.path.isfile(path):
            return
        candidates.append(path)
        seen.add(normalized)

    if system == "Darwin":
        for app in _DARWIN_APPS:
            add(app)
    elif system == "Windows":
        for name in _WINDOWS_NAMES:
            add(shutil.which(name))
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            for parts in _WINDOWS_INSTALLS:
                add(os.path.join(base, *parts) if base else None)
    else:
        for name in _LINUX_CANDIDATES:
            add(shutil.which(name))
        for path in _LINUX_PATHS:
            add(path)
        for base in ("/mnt/c/Program Files", "/mnt/c/Program Files (x86)"):
            for parts in _WINDOWS_INSTALLS:
                add(os.path.join(base, *parts))
    return candidates


def _chrome_debug_args(port: int) -> list[str]:
    return [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={chrome_debug_data_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def manual_chrome_debug_command(port: int = DEFAULT_BROWSER_CDP_PORT, system: str | None = None) -> str | None:
    system = system or platform.system()
    candidates = get_chrome_debug_candidates(system)
    if candidates:
        argv = [candidates[0], *_chrome_debug_args(port)]
        return subprocess.list2cmdline(argv) if system == "Windows" else shlex.join(argv)
    if system == "Darwin":
        data_dir = chrome_debug_data_dir()
        return (
            f'open -a "Google Chrome" --args --remote-debugging-port={port} '
            f'--user-data-dir="{data_dir}" --no-first-run --no-default-browser-check'
        )
    return None


def try_launch_chrome_debug(port: int = DEFAULT_BROWSER_CDP_PORT, system: str | None = None) -> bool:
    system = system or platform.system()
    candidates = get_chrome_debug_candidates(system)
    if not candidates:
        return False
    os.makedirs(chrome_debug_data_dir(), exist_ok=True)
    kwargs = {"start_new_session": True} if system != "Windows" else {}
    if system == "Windows":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs = {"creationflags": flags}
    for candidate in candidates:
        try:
            subprocess.Popen(
                [candidate, *_chrome_debug_args(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
            return True
        except Exception:
            continue
    return False


def manage_browser(action: str, *, url: Any = None, config: Any = None, launch: bool = True) -> dict[str, Any]:
    cmd = str(action or "status").strip().lower()
    if cmd == "status":
        current = current_cdp_url(config)
        return {"connected": bool(current), "url": current}
    if cmd == "disconnect":
        had_env = bool(os.environ.get("BROWSER_CDP_URL", "").strip())
        os.environ.pop("BROWSER_CDP_URL", None)
        fallback = current_cdp_url(config)
        return {
            "connected": bool(fallback),
            "url": fallback,
            "messages": ["browser tools reverted to configured default mode"] if had_env else [],
        }
    if cmd != "connect":
        raise ValueError("usage: /browser [connect|disconnect|status] [url]")

    cdp_url, port = normalize_cdp_url(url or DEFAULT_BROWSER_CDP_URL)
    messages: list[str] = []
    ready = is_browser_debug_ready(cdp_url, timeout=1.0)
    if ready:
        messages.append(f"Chromium-family browser is already listening on port {port}")
    elif cdp_url == DEFAULT_BROWSER_CDP_URL and launch:
        messages.append("Chromium-family browser isn't running with remote debugging - attempting to launch...")
        if try_launch_chrome_debug(port):
            for _ in range(10):
                if is_browser_debug_ready(cdp_url, timeout=1.0):
                    ready = True
                    break
                time.sleep(0.5)
            if ready:
                messages.append(f"Chromium-family browser launched and listening on port {port}")
            else:
                messages.append(f"Browser launched but port {port} is not responding yet")
        else:
            messages.append("Could not auto-launch a Chromium-family browser")
            manual = manual_chrome_debug_command(port)
            if manual:
                messages.append(f"Launch manually: {manual}")
    else:
        messages.append(f"Port {port} is not reachable at {cdp_url}")

    if not ready:
        return {"connected": False, "url": os.environ.get("BROWSER_CDP_URL", "").strip(), "messages": messages}
    os.environ["BROWSER_CDP_URL"] = cdp_url
    return {"connected": True, "url": cdp_url, "messages": messages}
