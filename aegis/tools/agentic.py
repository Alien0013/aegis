"""Higher-order tools: subagent spawning and image generation."""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from ..util import slugify
from .base import Tool, ToolContext, ToolResult


class SubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate a self-contained sub-task to a fresh child agent with its own context. "
        "Returns only the child's final answer. Use for research/exploration that would "
        "otherwise flood the main context."
    )
    groups = ["automation"]
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Complete, self-contained instructions."},
            "toolsets": {"type": "array", "items": {"type": "string"},
                         "description": "Toolsets the child may use (default: core)."},
        },
        "required": ["task"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..agent.agent import Agent
        from ..session import Session

        parent = ctx.agent
        depth = (getattr(parent, "_depth", 0) if parent else 0) + 1
        if depth > 2:
            return ToolResult.error("subagent depth limit reached (max 2).")

        config = ctx.config
        if config is None:
            return ToolResult.error("no config available for subagent.")
        child = Agent.create(config, session=Session.create(), cwd=ctx.cwd)
        child._depth = depth  # type: ignore[attr-defined]
        if args.get("toolsets"):
            child.config.data.setdefault("tools", {})["toolsets"] = args["toolsets"]
        ctx.emit_event(type="subagent_start", task=args["task"][:80])
        result = child.run(args["task"])
        return ToolResult.ok(result.content or "(no output)", display="subagent finished")


class ImageGenTool(Tool):
    name = "generate_image"
    description = "Generate an image from a text prompt (OpenAI-compatible /images/generations). Saves a PNG and returns its path."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "size": {"type": "string", "description": "e.g. 1024x1024"},
            "model": {"type": "string", "description": "image model (default gpt-image-1)."},
        },
        "required": ["prompt"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..providers import build_provider

        try:
            provider = build_provider(ctx.config)
            headers = {"Content-Type": "application/json", **provider.auth.headers()}
            payload = {
                "model": args.get("model", "gpt-image-1"),
                "prompt": args["prompt"],
                "size": args.get("size", "1024x1024"),
                "n": 1,
            }
            with httpx.Client(timeout=180) as c:
                r = c.post(f"{provider.base_url}/images/generations", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()["data"][0]
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"image generation failed (provider must support images): {e}")

        out_dir = ctx.cwd / "aegis_images"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{slugify(args['prompt'], 30)}-{int(time.time())}.png"
        if data.get("b64_json"):
            path.write_bytes(base64.b64decode(data["b64_json"]))
        elif data.get("url"):
            with httpx.Client(timeout=120) as c:
                path.write_bytes(c.get(data["url"]).content)
        else:
            return ToolResult.error("no image data returned.")
        return ToolResult.ok(f"saved image to {path}", display=f"image -> {path.name}")


def agentic_tools() -> list[Tool]:
    return [SubagentTool(), ImageGenTool()]
