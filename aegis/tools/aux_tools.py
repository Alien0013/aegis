"""Auxiliary-model tools: cheap side-model calls that don't belong on the main agent loop.

- ``vision_analyze`` — describe/answer questions about an image with the vision aux model.
- ``web_extract`` — fetch a URL and return an aux-model summary/extraction (vs raw ``web_fetch``).

Both resolve their model via ``build_aux_provider(config, purpose=...)`` so they honor the
per-task ``auxiliary.vision`` / ``auxiliary.web_extract`` slots (falling back to the main model).
"""

from __future__ import annotations

from pathlib import Path

from ..types import Message
from .base import Tool, ToolContext, ToolResult


def _aux_provider(ctx: ToolContext, purpose: str):
    from ..providers.registry import build_aux_provider
    return build_aux_provider(getattr(ctx, "config", None), purpose=purpose)


class VisionAnalyzeTool(Tool):
    name = "vision_analyze"
    description = (
        "Analyze an image with a vision model and answer a question about it. `image` may be a "
        "local file path, an http(s) URL, or a base64 data URL. Use for screenshots, diagrams, "
        "photos, or charts — not for generating images."
    )
    groups = ["network"]
    toolset = "vision"
    parameters = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "File path, http(s) URL, or base64 data URL."},
            "prompt": {"type": "string", "description": "What to look for / the question to answer."},
        },
        "required": ["image"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        src = (args.get("image") or "").strip()
        if not src:
            return ToolResult.error("image is required (path, url, or data URL).")
        prompt = args.get("prompt") or "Describe this image in detail."
        try:
            data_url = self._to_data_url(src, ctx)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"could not load image: {e}")
        try:
            provider = _aux_provider(ctx, "vision")
            resp = provider.complete(
                [Message.system("You are a precise visual analyst. Be concise and factual."),
                 Message.user(prompt, images=[data_url])],
                tools=None, stream=False,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"vision model call failed: {e}")
        text = (resp.text or "").strip() or "(no description returned)"
        return ToolResult.ok(text, display=f"analyzed image ({prompt[:40]})")

    @staticmethod
    def _to_data_url(src: str, ctx: ToolContext) -> str:
        if src.startswith("data:image/"):
            return src
        if src.startswith(("http://", "https://")):
            import base64

            from .. import net_safety
            blocked = net_safety.guard(src, getattr(ctx, "config", None))
            if blocked:
                raise ValueError(blocked)
            r = net_safety.request("GET", src, getattr(ctx, "config", None), timeout=30)
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/png").split(";")[0]
            b64 = base64.b64encode(r.content).decode()
            return f"data:{mime};base64,{b64}"
        from ..util import encode_image
        path = Path(src).expanduser()
        if not path.is_file():
            raise FileNotFoundError(src)
        return encode_image(path)


class WebExtractTool(Tool):
    name = "web_extract"
    description = (
        "Fetch a web page and return a focused, aux-model summary/extraction of it (cheaper on "
        "context than raw web_fetch for large pages). Optionally pass `query` to weight the "
        "extraction toward a specific question."
    )
    groups = ["network"]
    toolset = "web"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "query": {"type": "string", "description": "Optional focus for the extraction."},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        url = (args.get("url") or "").strip()
        if not url:
            return ToolResult.error("url is required.")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        from .. import net_safety
        blocked = net_safety.guard(url, getattr(ctx, "config", None))
        if blocked:
            return ToolResult.error(blocked)
        try:
            r = net_safety.request("GET", url, getattr(ctx, "config", None), timeout=30)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            body = r.text
        except net_safety.BlockedURL as e:
            return ToolResult.error(str(e))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"fetch failed: {e}")
        from .builtin import _html_to_text
        text = _html_to_text(body) if "html" in ctype else body
        text = text[:60_000]
        query = args.get("query") or ""
        instruction = (
            "Extract the key information from this web page as concise, factual bullet points. "
            "Preserve specific facts, names, numbers, and any code. Drop boilerplate/nav/ads."
            + (f"\nFocus on: {query}" if query else "")
        )
        try:
            provider = _aux_provider(ctx, "web_extract")
            resp = provider.complete(
                [Message.system(instruction), Message.user(text)], tools=None, stream=False,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"extract model call failed: {e}")
        out = (resp.text or "").strip() or "(no content extracted)"
        return ToolResult.ok(out, display=f"extracted {url[:50]}")


def aux_tools() -> list[Tool]:
    return [VisionAnalyzeTool(), WebExtractTool()]
