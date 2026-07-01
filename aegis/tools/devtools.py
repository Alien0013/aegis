"""Developer tools: GitHub (gh CLI) and deferred-tool bridge helpers."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from typing import Any

from ..util import truncate
from .base import Tool, ToolContext, ToolResult


TOOL_SEARCH_NAME = "tool_search"
TOOL_DESCRIBE_NAME = "tool_describe"
TOOL_CALL_NAME = "tool_call"
BRIDGE_TOOL_NAMES = frozenset({TOOL_SEARCH_NAME, TOOL_DESCRIBE_NAME, TOOL_CALL_NAME})
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Low-level harness tools that must stay directly callable even if an over-broad
# selector such as glob:* or toolset:core is configured for schema deferral.
DIRECT_EXECUTION_TOOL_NAMES = frozenset({
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "glob",
    "search",
    "bash",
    "system_status",
    "secret",
    "todo_write",
    "memory",
    "skill",
    "apply_patch",
    "clarify",
    "spawn_subagent",
    "execute_code",
    "session_search",
    "agent_state",
    "process",
    "terminal",
    "patch",
    "search_files",
    "delegate_task",
    "todo",
    "read_terminal",
    "skills_list",
    "skill_view",
})


def is_bridge_or_direct_tool_name(name: str) -> bool:
    return name in BRIDGE_TOOL_NAMES or name in DIRECT_EXECUTION_TOOL_NAMES


def _agent_available_tools(agent) -> list[Tool]:
    if not (agent and getattr(agent, "registry", None)):
        return []
    config = getattr(agent, "config", None)
    toolsets = config.get("tools.toolsets", ["core"]) if config is not None else ["core"]
    disabled = config.get("tools.disabled", []) if config is not None else []
    return agent.registry.available(toolsets or ["core"], disabled=disabled)


def _agent_candidate_names(agent, available: list[Tool] | None = None) -> set[str]:
    helper = getattr(agent, "deferred_tool_candidate_names", None)
    if callable(helper):
        return set(helper(available))
    helper = getattr(agent, "deferred_tool_names", None)
    if callable(helper):
        return set(helper(available))
    return set()


def _coerce_tool_call_args(args: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    name = str(args.get("name") or "").strip()
    if not name:
        return "", {}, "tool_call requires a 'name' argument"
    if name in BRIDGE_TOOL_NAMES:
        return "", {}, f"tool_call cannot invoke '{name}' because it is a bridge tool"
    raw_args = args.get("arguments", {})
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            return "", {}, f"tool_call 'arguments' is not valid JSON: {exc}"
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return "", {}, "tool_call 'arguments' must be an object"
    return name, raw_args, ""


def _bounded_search_limit(value: Any, default: int = 5, maximum: int = 20) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(maximum, limit))


def _tool_search_text(tool: Tool) -> str:
    params = getattr(tool, "parameters", {}) or {}
    props = params.get("properties") if isinstance(params, dict) else {}
    param_names = " ".join(str(key) for key in props) if isinstance(props, dict) else ""
    name_words = tool.name.replace("_", " ").replace("-", " ").replace(".", " ")
    return f"{tool.name} {name_words} {tool.description} {param_names}"


def _tokenize_search_text(text: str) -> list[str]:
    return [part.lower() for part in _TOKEN_RE.findall(text or "")]


def _tool_matches_query(tool: Tool, query: str) -> bool:
    query = str(query or "").strip().lower()
    text = _tool_search_text(tool).lower()
    if query in text:
        return True
    tokens = [part for part in query.replace("_", " ").replace("-", " ").split() if part]
    return bool(tokens) and all(token in text for token in tokens)


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    doc_freq: dict[str, int],
    doc_count: int,
    avg_doc_len: float,
) -> float:
    if not doc_tokens:
        return 0.0
    term_freq: dict[str, int] = {}
    for token in doc_tokens:
        term_freq[token] = term_freq.get(token, 0) + 1
    score = 0.0
    doc_len = len(doc_tokens)
    for token in query_tokens:
        df = doc_freq.get(token, 0)
        tf = term_freq.get(token, 0)
        if not df or not tf:
            continue
        idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
        norm = tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * doc_len / max(avg_doc_len, 1.0)))
        score += idf * norm
    return score


def _rank_tool_search_hits(tools: list[Tool], query: str) -> list[Tool]:
    query_tokens = _tokenize_search_text(query)
    if not query_tokens:
        return []
    tokenized = [(tool, _tokenize_search_text(_tool_search_text(tool))) for tool in tools]
    doc_freq: dict[str, int] = {}
    for _, tokens in tokenized:
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    avg_doc_len = sum(len(tokens) for _, tokens in tokenized) / max(len(tokenized), 1)
    scored: list[tuple[float, int, Tool]] = []
    for index, (tool, tokens) in enumerate(tokenized):
        score = _bm25_score(query_tokens, tokens, doc_freq, len(tokenized), avg_doc_len)
        if score > 0:
            scored.append((score, index, tool))
    if not scored:
        return [tool for tool in tools if _tool_matches_query(tool, query)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tool for _, _, tool in scored]


def _tool_source_info(tool: Tool) -> tuple[str, str]:
    toolset = str(getattr(tool, "toolset", "") or "core").strip() or "core"
    manifest_id = str(getattr(tool, "manifest_id", "") or getattr(tool, "_aegis_manifest_id", "") or "").strip()
    raw_source = str(getattr(tool, "source", "") or getattr(tool, "_aegis_source", "") or "").strip().lower()
    if raw_source == "mcp" or toolset == "mcp" or toolset.startswith("mcp"):
        return "mcp", manifest_id or toolset
    if raw_source and raw_source not in {"builtin", "tool"}:
        return raw_source, manifest_id or toolset
    if toolset != "core":
        return "plugin", manifest_id or toolset
    return raw_source or "builtin", manifest_id or toolset


def _format_search_hit(tool: Tool) -> dict[str, Any]:
    source, source_name = _tool_source_info(tool)
    return {
        "name": tool.name,
        "source": source,
        "source_name": source_name,
        "description": (tool.description or "")[:400],
    }


def _tool_schema_payload(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }


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
    name = TOOL_SEARCH_NAME
    description = "Search YOUR available tools by keyword (self-discovery). Returns matching tool names + descriptions."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "Maximum number of matches to return. Default 5."},
        },
        "required": ["query"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult.error("query is required")
        limit = _bounded_search_limit(args.get("limit"))
        agent = ctx.agent
        tools = _agent_available_tools(agent)
        candidate_names = _agent_candidate_names(agent, tools)
        catalog_tools = [tool for tool in tools if tool.name in candidate_names]
        all_hits = _rank_tool_search_hits(catalog_tools, query)
        hits = all_hits[:limit]
        body = {
            "query": query,
            "total_available": len(catalog_tools),
            "matches": [_format_search_hit(t) for t in hits],
        }
        if not hits:
            return ToolResult.ok(
                json.dumps(body, indent=1),
                display="tool_search",
                data=body,
            )
        # Activate any deferred hits: their full schemas join the request from the next
        # model call on (session-sticky), so the model can call them immediately after.
        deferred = set(agent.deferred_tool_names(tools)) if agent and hasattr(agent, "deferred_tool_names") else set()
        activated = [t for t in hits if t.name in deferred]
        if agent is not None and activated:
            agent.activated_tools.update(t.name for t in activated)
        if activated:
            body["activated"] = [tool.name for tool in activated]
            body["schemas"] = [_tool_schema_payload(tool) for tool in activated]
        return ToolResult.ok(
            json.dumps(body, indent=1),
            display=f"{len(hits)} tool(s)" + (f", {len(activated)} activated" if activated else ""),
            data=body,
        )


class ToolDescribeTool(Tool):
    name = TOOL_DESCRIBE_NAME
    description = "Load the full parameter schema for one deferred tool returned by tool_search."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.error("name is required")
        if name in BRIDGE_TOOL_NAMES:
            return ToolResult.error(f"`{name}` is a bridge tool; call it directly.")
        agent = ctx.agent
        if not (agent and getattr(agent, "registry", None)):
            return ToolResult.error("tool_describe requires an active agent registry")
        available = _agent_available_tools(agent)
        available_by_name = {t.name: t for t in available}
        if name not in available_by_name:
            return ToolResult.error(f"`{name}` is not available in this session")
        candidates = _agent_candidate_names(agent, available)
        if name not in candidates:
            return ToolResult.error(f"`{name}` is not a deferred tool; call it directly if it is visible.")
        tool = available_by_name[name]
        if hasattr(agent, "activated_tools"):
            agent.activated_tools.add(name)
        body = _tool_schema_payload(tool)
        return ToolResult.ok(json.dumps(body, indent=1), display=f"schema {name}", data=body)


class ToolCallTool(Tool):
    name = TOOL_CALL_NAME
    description = "Invoke a deferred tool by name with arguments matching its schema."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["name", "arguments"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        name, call_args, error = _coerce_tool_call_args(args)
        if error:
            return ToolResult.error(error)
        agent = ctx.agent
        if not (agent and getattr(agent, "registry", None)):
            return ToolResult.error("tool_call requires an active agent registry")
        available = _agent_available_tools(agent)
        available_names = {t.name for t in available}
        if name not in available_names:
            return ToolResult.error(f"`{name}` is not available in this session")
        if is_bridge_or_direct_tool_name(name):
            return ToolResult.error(f"`{name}` is not a deferred tool; call it directly.")
        candidates = _agent_candidate_names(agent, available)
        if name not in candidates:
            return ToolResult.error(f"`{name}` is not a deferred tool; call it directly if it is visible.")
        return ctx.dispatch_tool(
            name,
            call_args,
            registry=agent.registry,
            permissions=agent.permissions,
        )


def dev_tools() -> list[Tool]:
    return [GithubTool(), ToolSearchTool(), ToolDescribeTool(), ToolCallTool()]
