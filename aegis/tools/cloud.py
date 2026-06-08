"""Provider-agnostic 'tool gateway' backends: cloud image gen, cloud browser, status.

Mirrors Hermes' Nous Portal tool gateway but self-hosted / bring-your-own-keys.
"""

from __future__ import annotations

import os
import time

import httpx

from ..util import slugify
from .base import Tool, ToolContext, ToolResult


class CloudImageTool(Tool):
    name = "cloud_image"
    description = "Generate an image via fal.ai (FAL_KEY). Saves a PNG and returns its path."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "description": "fal model id (default fal-ai/flux/schnell)"},
        },
        "required": ["prompt"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        key = os.environ.get("FAL_KEY")
        if not key:
            return ToolResult.error("set FAL_KEY to use cloud_image (fal.ai), or use generate_image.")
        model = args.get("model", "fal-ai/flux/schnell")
        try:
            with httpx.Client(timeout=180) as c:
                r = c.post(f"https://fal.run/{model}", headers={"Authorization": f"Key {key}"},
                           json={"prompt": args["prompt"]})
                r.raise_for_status()
                data = r.json()
            url = data["images"][0]["url"]
            img = httpx.get(url, timeout=120).content
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"fal image failed: {e}")
        out = ctx.cwd / f"{slugify(args['prompt'], 30)}-{int(time.time())}.png"
        out.write_bytes(img)
        return ToolResult.ok(f"saved image to {out}", display=f"fal → {out.name}")


class CloudBrowserTool(Tool):
    name = "cloud_browser"
    description = "Fetch a JavaScript-rendered page via a cloud browser (browserless). Needs BROWSERLESS_URL."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        base = os.environ.get("BROWSERLESS_URL")
        if not base:
            return ToolResult.error("set BROWSERLESS_URL (a browserless/Browser-Use endpoint) "
                                    "to use cloud_browser, or use the local `browser` tool.")
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(f"{base.rstrip('/')}/content", json={"url": args["url"]})
                r.raise_for_status()
                html = r.text
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"cloud browser failed: {e}")
        from .builtin import _html_to_text
        return ToolResult.ok(_html_to_text(html)[:20_000], display="cloud_browser")


def tools_status(config) -> dict:
    """Report which 'tool gateway' backends are configured."""
    def has(*envs):
        return any(os.environ.get(e) for e in envs)
    return {
        "web_search": config.get("web.search_backend", "auto") + (
            " (brave)" if has("BRAVE_API_KEY") else " (tavily)" if has("TAVILY_API_KEY")
            else " (serper)" if has("SERPER_API_KEY") else " (duckduckgo)"),
        "image": "fal" if has("FAL_KEY") else "provider (generate_image)",
        "tts": "provider audio (speak)" if config.get("model.provider") else "—",
        "cloud_browser": "browserless" if has("BROWSERLESS_URL") else "local playwright",
        "terminal_backend": config.get("tools.terminal_backend", "local"),
    }


def cmd_tools_status(args, config) -> int:
    for k, v in tools_status(config).items():
        print(f"  {k:<16} {v}")
    return 0


def cloud_tools() -> list[Tool]:
    return [CloudImageTool(), CloudBrowserTool()]
