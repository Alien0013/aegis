"""web_verify — close the frontend loop: load the running app and check it actually works.

Tests close the loop for backend code; for web/UI work the equivalent is *open the page
and look at it*. This tool loads a URL in a headless browser and reports whether the page
rendered without console/page errors, and (optionally) whether an expected selector or
text is present. It can start the dev server first (``start``), wait for it to come up,
run the checks, and tear it back down — so "I changed the React app" can be followed by
"…and confirmed it still renders" in one step.

Playwright is optional: the tool reports a clear, non-fatal message when it (or the dev
server) is unavailable, so it never blocks a session.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..util import truncate
from .base import Tool, ToolContext, ToolResult

_DEFAULT_TIMEOUT_MS = 30000


@dataclass
class Verdict:
    passed: bool
    reasons: list[str]


def evaluate(*, console_errors: list[str], page_errors: list[str], text: str,
            expect_text: str | None, selector_found: bool | None,
            expect_selector: str | None, allow_console_errors: bool) -> Verdict:
    """Pure pass/fail logic — the page passes iff every requested check holds."""
    reasons: list[str] = []
    if not allow_console_errors and (console_errors or page_errors):
        n = len(console_errors) + len(page_errors)
        reasons.append(f"{n} console/page error(s)")
    if expect_text and expect_text.lower() not in (text or "").lower():
        reasons.append(f"expected text not found: {expect_text!r}")
    if expect_selector and selector_found is False:
        reasons.append(f"expected selector not found: {expect_selector!r}")
    return Verdict(passed=not reasons, reasons=reasons or ["page rendered cleanly"])


def _wait_for_server(url: str, timeout_s: float) -> bool:
    """Poll ``url`` until it answers (any HTTP status) or the deadline passes."""
    try:
        import httpx
    except ImportError:
        time.sleep(min(timeout_s, 3.0))   # no probe available — give it a moment
        return True
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=2.0)
            return True
        except Exception:  # noqa: BLE001 — not up yet
            time.sleep(0.4)
    return False


class WebVerifyTool(Tool):
    name = "web_verify"
    description = (
        "Verify a web UI actually works: load a URL in a headless browser and report whether "
        "it rendered without console/page errors, and optionally whether expect_text / "
        "expect_selector are present. Optionally start the dev server first (start='npm run "
        "dev'), wait for it, check, and shut it down. Use after editing frontend code to "
        "confirm the page still renders — the UI equivalent of running tests."
    )
    groups = ["network", "automation"]
    toolset = "browser"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page to load, e.g. http://localhost:3000"},
            "start": {"type": "string", "description": "Optional shell command to launch the dev server first."},
            "expect_text": {"type": "string", "description": "Text that must appear on the page."},
            "expect_selector": {"type": "string", "description": "CSS selector that must exist."},
            "allow_console_errors": {"type": "boolean", "description": "Don't fail on console errors (default false)."},
            "ready_timeout": {"type": "integer", "description": "Seconds to wait for the dev server (default 30)."},
        },
        "required": ["url"],
    }

    def available(self):
        import importlib.util
        if importlib.util.find_spec("playwright") is None:
            return False, "needs `pip install 'aegis-agent-harness[browser]'` + `playwright install chromium`"
        return True, ""

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not url:
            return ToolResult.error("web_verify requires a url")
        from ..net_safety import guard
        blocked = guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)

        proc = None
        ready_timeout = float(args.get("ready_timeout") or 30)
        try:
            if args.get("start"):
                proc = subprocess.Popen(
                    str(args["start"]), shell=True, cwd=str(ctx.cwd or Path.cwd()),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if not _wait_for_server(url, ready_timeout):
                    return ToolResult.error(f"dev server did not become reachable at {url} "
                                            f"within {ready_timeout:.0f}s")
            return self._verify(url, args, ctx)
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _verify(self, url: str, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ToolResult.error("web_verify needs `pip install playwright` + `playwright install chromium`")

        headless = bool(ctx.config.get("browser.headless", True)) if ctx.config else True
        console_errors: list[str] = []
        page_errors: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            page = browser.new_context().new_page()
            page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}")
                    if m.type in ("error",) else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            try:
                page.goto(url, wait_until="networkidle", timeout=_DEFAULT_TIMEOUT_MS)
            except Exception as e:  # noqa: BLE001
                browser.close()
                return ToolResult.error(f"failed to load {url}: {e}")
            selector_found: bool | None = None
            if args.get("expect_selector"):
                try:
                    page.wait_for_selector(str(args["expect_selector"]), timeout=10000)
                    selector_found = True
                except Exception:  # noqa: BLE001
                    selector_found = False
            text = ""
            try:
                text = page.inner_text("body")
            except Exception:  # noqa: BLE001
                pass
            browser.close()

        verdict = evaluate(
            console_errors=console_errors, page_errors=page_errors, text=text,
            expect_text=args.get("expect_text"), selector_found=selector_found,
            expect_selector=args.get("expect_selector"),
            allow_console_errors=bool(args.get("allow_console_errors")),
        )
        head = ("✓ web_verify passed" if verdict.passed else "✗ web_verify FAILED") + f" — {url}"
        body = [head, "  " + "; ".join(verdict.reasons)]
        if console_errors or page_errors:
            body.append("console/page errors:")
            body.extend("  " + truncate(e, 300) for e in (console_errors + page_errors)[:10])
        result = "\n".join(body)
        display = "web_verify: pass" if verdict.passed else "web_verify: FAIL"
        return ToolResult(content=result, display=display, is_error=not verdict.passed)


def web_verify_tools() -> list[Tool]:
    return [WebVerifyTool()]
