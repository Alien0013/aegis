"""Browser automation (Playwright) and OS-level computer-use (pyautogui).

Both deps are optional: `pip install 'aegis-agent-harness[browser]'` then `playwright
install chromium`, and `pip install 'aegis-agent-harness[computer]'`. Tools fail with a clear message
if the dep is missing rather than at import time.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from ..util import truncate
from .base import Tool, ToolContext, ToolResult

# Safety gates (from computer-use research): refuse destructive key combos / typed payloads.
_BLOCKED_KEYS = {"cmd+shift+backspace", "cmd+option+backspace", "cmd+ctrl+q", "cmd+shift+q"}
_BLOCKED_TYPE = [r"curl\s*\|\s*(bash|sh)", r"wget\s*\|\s*(bash|sh)", r"sudo\s+rm\s+-[rf]",
                 r"rm\s+-rf\s+/", r":\(\)\{:\|:&\};"]


class BrowserTool(Tool):
    name = "browser"
    description = (
        "Drive a headless browser. actions: navigate(url) | text (readable page text) | "
        "html | click(selector) | type(selector,text) | screenshot(path) | back. State "
        "persists across calls within a session."
    )
    groups = ["network", "automation"]
    toolset = "browser"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["navigate", "text", "html", "click", "type", "screenshot", "back"]},
            "url": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = threading.Lock()

    def _ensure(self, ctx: ToolContext):
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError("browser tool needs `pip install playwright` + `playwright install chromium`") from e
        headless = True
        if ctx.config is not None:
            headless = bool(ctx.config.get("browser.headless", True))
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page = self._browser.new_context(accept_downloads=True).new_page()

    def run(self, args, ctx: ToolContext) -> ToolResult:
        action = args["action"]
        with self._lock:
            try:
                self._ensure(ctx)
                page = self._page
                if action == "navigate":
                    page.goto(args["url"], wait_until="domcontentloaded", timeout=30000)
                    return ToolResult.ok(f"navigated to {page.url}\ntitle: {page.title()}",
                                         display=f"browser → {args['url'][:50]}")
                if action == "text":
                    body = page.inner_text("body")
                    return ToolResult.ok(truncate(body, 20_000), display="page text")
                if action == "html":
                    return ToolResult.ok(truncate(page.content(), 20_000), display="page html")
                if action == "click":
                    page.click(args["selector"], timeout=10000)
                    return ToolResult.ok(f"clicked {args['selector']}", display="click")
                if action == "type":
                    page.fill(args["selector"], args.get("text", ""))
                    return ToolResult.ok(f"typed into {args['selector']}", display="type")
                if action == "screenshot":
                    path = Path(args.get("path") or (ctx.cwd / f"screenshot-{int(time.time())}.png"))
                    page.screenshot(path=str(path), full_page=False)
                    return ToolResult.ok(f"saved screenshot to {path}", display=f"shot → {path.name}")
                if action == "back":
                    page.go_back()
                    return ToolResult.ok(f"back to {page.url}", display="back")
                return ToolResult.error(f"unknown action '{action}'")
            except Exception as e:  # noqa: BLE001
                return ToolResult.error(f"browser error: {e}")


class ComputerTool(Tool):
    name = "computer"
    description = (
        "Control the local screen/keyboard/mouse (pyautogui). actions: screenshot | "
        "click(x,y) | move(x,y) | type(text) | key(combo) | scroll(amount). Destructive "
        "keys and shell-injection payloads are blocked."
    )
    groups = ["runtime", "automation"]
    toolset = "computer"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["screenshot", "click", "move", "type", "key", "scroll"]},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "text": {"type": "string"}, "combo": {"type": "string"},
            "amount": {"type": "integer"}, "path": {"type": "string"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            import pyautogui
        except ImportError as e:  # noqa: BLE001
            return ToolResult.error("computer tool needs `pip install pyautogui`")
        action = args["action"]
        try:
            if action == "screenshot":
                path = Path(args.get("path") or (ctx.cwd / f"screen-{int(time.time())}.png"))
                pyautogui.screenshot(str(path))
                return ToolResult.ok(f"saved screen to {path}", display="screenshot")
            if action == "click":
                pyautogui.click(args["x"], args["y"])
                return ToolResult.ok(f"clicked ({args['x']},{args['y']})", display="click")
            if action == "move":
                pyautogui.moveTo(args["x"], args["y"])
                return ToolResult.ok("moved", display="move")
            if action == "type":
                t = args.get("text", "")
                if any(re.search(p, t) for p in _BLOCKED_TYPE):
                    return ToolResult.error("blocked: dangerous payload")
                pyautogui.typewrite(t, interval=0.01)
                return ToolResult.ok("typed", display="type")
            if action == "key":
                combo = args.get("combo", "").lower()
                if combo in _BLOCKED_KEYS:
                    return ToolResult.error(f"blocked key combo: {combo}")
                pyautogui.hotkey(*combo.split("+"))
                return ToolResult.ok(f"pressed {combo}", display="key")
            if action == "scroll":
                pyautogui.scroll(int(args.get("amount", -300)))
                return ToolResult.ok("scrolled", display="scroll")
            return ToolResult.error(f"unknown action '{action}'")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"computer error: {e}")


def browser_tools() -> list[Tool]:
    return [BrowserTool(), ComputerTool()]
