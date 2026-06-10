"""Higher-order tools: subagent spawning and image generation."""

from __future__ import annotations

import base64
import threading
import time

import httpx

from ..types import new_id
from ..util import slugify
from .base import Tool, ToolContext, ToolResult

# Process-global registry of spawned subagents (id -> {status, task}) for observability and a
# bounded view of recent children. Capped so it can't grow without bound.
_REGISTRY: dict[str, dict] = {}
_REG_LOCK = threading.Lock()


def _register(sid: str, **fields) -> None:
    with _REG_LOCK:
        _REGISTRY.setdefault(sid, {}).update(fields)
        if len(_REGISTRY) > 200:                       # drop oldest
            for k in list(_REGISTRY)[:len(_REGISTRY) - 200]:
                _REGISTRY.pop(k, None)


class SubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate self-contained sub-task(s) to fresh child agents, each with its own context. "
        "Pass `task` for one, or `tasks` (array) to run several IN PARALLEL (bounded). Returns "
        "each child's final answer. Use for research/exploration/fan-out that would otherwise "
        "flood the main context."
    )
    groups = ["automation"]
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "One complete, self-contained instruction."},
            "tasks": {"type": "array", "items": {"type": "string"},
                      "description": "Several self-contained instructions, run in parallel."},
            "toolsets": {"type": "array", "items": {"type": "string"},
                         "description": "Toolsets the children may use (default: core)."},
        },
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
        tasks = list(args.get("tasks") or ([] if args.get("task") is None else [args["task"]]))
        tasks = [t for t in tasks if isinstance(t, str) and t.strip()]
        if not tasks:
            return ToolResult.error("provide `task` (string) or `tasks` (array of strings).")
        toolsets = args.get("toolsets")

        def _one(task: str) -> str:
            sid = new_id("sub")
            _register(sid, status="running", task=task[:80])
            ctx.emit_event(type="subagent_start", id=sid, task=task[:80])
            try:
                child = Agent.create(config, session=Session.create(), cwd=ctx.cwd)
                child._depth = depth  # type: ignore[attr-defined]
                if toolsets:
                    child.config.data.setdefault("tools", {})["toolsets"] = toolsets
                out = (child.run(task).content or "(no output)")
                _register(sid, status="done")
                ctx.emit_event(type="subagent_done", id=sid, status="done")
                return out
            except Exception as e:  # noqa: BLE001 - isolate one child's failure
                _register(sid, status="error")
                ctx.emit_event(type="subagent_done", id=sid, status="error")
                return f"[subagent error] {e}"

        if len(tasks) == 1:
            return ToolResult.ok(_one(tasks[0]), display="subagent finished")
        cap = max(1, int(config.get("agent.subagent_concurrency", 4) or 1))
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(cap, len(tasks))) as ex:
            results = list(ex.map(_one, tasks))
        body = "\n\n".join(f"## subagent {i + 1}\n{r}" for i, r in enumerate(results))
        return ToolResult.ok(body, display=f"{len(tasks)} subagents finished")


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
