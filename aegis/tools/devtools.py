"""Developer tools: GitHub (gh CLI) and tool-search (agent self-discovery)."""

from __future__ import annotations

import shutil
import subprocess

from ..util import truncate
from .base import Tool, ToolContext, ToolResult


class GithubTool(Tool):
    name = "github"
    description = ("Interact with GitHub via the gh CLI. actions: issues | prs | view(number) | "
                  "checks(number) | create_issue(title, body) | create_pr(title, body).")
    groups = ["network", "runtime"]
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["issues", "prs", "view", "checks", "create_issue", "create_pr"]},
            "number": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        if not shutil.which("gh"):
            return ToolResult.error("gh CLI not found (install GitHub CLI + `gh auth login`).")
        a = args["action"]
        cmd = {
            "issues": ["gh", "issue", "list"],
            "prs": ["gh", "pr", "list"],
            "view": ["gh", "pr", "view", args.get("number", "")],
            "checks": ["gh", "pr", "checks", args.get("number", "")],
            "create_issue": ["gh", "issue", "create", "-t", args.get("title", ""), "-b", args.get("body", "")],
            "create_pr": ["gh", "pr", "create", "-t", args.get("title", ""), "-b", args.get("body", "")],
        }.get(a)
        if not cmd:
            return ToolResult.error(f"unknown action {a}")
        try:
            r = subprocess.run(cmd, cwd=str(ctx.cwd), capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return ToolResult.error("gh timed out")
        out = (r.stdout or r.stderr).strip() or "(no output)"
        return ToolResult(content=truncate(out, 15_000), is_error=r.returncode != 0,
                          display=f"gh {a}")


class ToolSearchTool(Tool):
    name = "tool_search"
    description = "Search YOUR available tools by keyword (self-discovery). Returns matching tool names + descriptions."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        import json
        q = args["query"].lower()
        agent = ctx.agent
        if agent and agent.registry:
            tools = agent.registry.available(
                agent.config.get("tools.toolsets", ["core"]),
                disabled=agent.config.get("tools.disabled", []),
            )
        else:
            tools = []
        hits = [t for t in tools if q in t.name.lower() or q in t.description.lower()]
        if not hits:
            return ToolResult.ok("(no matching tools)", display="tool_search")
        # Activate any deferred hits: their full schemas join the request from the next
        # model call on (session-sticky), so the model can call them immediately after.
        deferred = agent.deferred_tool_names() if agent and hasattr(agent, "deferred_tool_names") else set()
        activated = [t for t in hits if t.name in deferred]
        if agent is not None and activated:
            agent.activated_tools.update(t.name for t in activated)
        lines = [f"{t.name}: {t.description.splitlines()[0]}" for t in hits]
        for t in activated:
            lines.append(f"\nactivated `{t.name}` — schema now loaded:\n"
                         + json.dumps({"name": t.name, "parameters": t.parameters}, indent=1))
        return ToolResult.ok("\n".join(lines),
                             display=f"{len(hits)} tool(s)"
                                     + (f", {len(activated)} activated" if activated else ""))


def dev_tools() -> list[Tool]:
    return [GithubTool(), ToolSearchTool()]
