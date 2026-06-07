"""Additional built-in tools that Hermes/OpenClaw ship with: patch, download,
generic HTTP, and agent-callable task scheduling.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..util import truncate
from .base import Tool, ToolContext, ToolResult


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "Apply a unified diff (git/patch format) to files in the working directory. Use for multi-file or multi-hunk edits."
    groups = ["fs"]
    parameters = {
        "type": "object",
        "properties": {"patch": {"type": "string", "description": "a unified diff"}},
        "required": ["patch"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        patch = args["patch"]
        if not patch.endswith("\n"):
            patch += "\n"
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
            f.write(patch)
            tmp = f.name
        # try git apply at -p1 then -p0, then GNU patch
        for cmd in (["git", "apply", "--whitespace=nowarn", tmp],
                    ["git", "apply", "-p0", "--whitespace=nowarn", tmp],
                    ["patch", "-p1", "-i", tmp]):
            try:
                r = subprocess.run(cmd, cwd=str(ctx.cwd), capture_output=True, text=True, timeout=60)
            except FileNotFoundError:
                continue
            if r.returncode == 0:
                Path(tmp).unlink(missing_ok=True)
                return ToolResult.ok(f"applied patch with `{' '.join(cmd[:2])}`", display="patch applied")
        Path(tmp).unlink(missing_ok=True)
        return ToolResult.error("patch did not apply cleanly (check paths and context lines)")


class DownloadTool(Tool):
    name = "download"
    description = "Download a URL to a local file."
    groups = ["network", "fs"]
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "path": {"type": "string", "description": "destination (default: basename in cwd)"},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        url = args["url"]
        dest = Path(args["path"]) if args.get("path") else \
            ctx.cwd / (Path(urlparse(url).path).name or "download.bin")
        if not dest.is_absolute():
            dest = ctx.cwd / dest
        try:
            with httpx.Client(timeout=120, follow_redirects=True) as c:
                r = c.get(url)
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"download failed: {e}")
        return ToolResult.ok(f"downloaded {len(r.content)} bytes to {dest}", display=f"↓ {dest.name}")


class HttpRequestTool(Tool):
    name = "http_request"
    description = "Make an arbitrary HTTP request (GET/POST/PUT/DELETE) and return status + body. For APIs."
    groups = ["network"]
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "headers": {"type": "object"},
            "body": {"type": "string", "description": "raw request body (e.g. JSON string)"},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        try:
            with httpx.Client(timeout=60, follow_redirects=True) as c:
                r = c.request(args.get("method", "GET"), args["url"],
                              headers=args.get("headers"), content=args.get("body"))
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"request failed: {e}")
        return ToolResult(
            content=f"HTTP {r.status_code}\n{truncate(r.text, 20_000)}",
            is_error=r.status_code >= 400,
            display=f"{args.get('method', 'GET')} {r.status_code}",
        )


class ScheduleTaskTool(Tool):
    name = "schedule_task"
    description = "Schedule a recurring task. schedule: interval ('30m','2h','@daily') or 5-field cron. Runs when the scheduler/gateway is active."
    groups = ["automation"]
    parameters = {
        "type": "object",
        "properties": {
            "schedule": {"type": "string"},
            "prompt": {"type": "string", "description": "instructions to run on schedule"},
        },
        "required": ["schedule", "prompt"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..cron import CronStore
        job = CronStore().add(args["schedule"], args["prompt"])
        return ToolResult.ok(f"scheduled {job.id} [{job.schedule}] (run `aegis cron run` to activate)",
                             display=f"scheduled {job.id}")


def extra_tools() -> list[Tool]:
    return [ApplyPatchTool(), DownloadTool(), HttpRequestTool(), ScheduleTaskTool()]
