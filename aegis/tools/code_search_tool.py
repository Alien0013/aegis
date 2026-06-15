"""code_search tool: natural-language semantic search over the codebase.

Embeds the query and finds the most relevant code chunks by meaning (Cursor/Aider
style), backed by :mod:`aegis.semantic_index`. When no embeddings provider is
configured it transparently falls back to the structural repo map so the tool is
always useful.
"""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolContext, ToolResult


class CodeSearchTool(Tool):
    name = "code_search"
    description = (
        "Semantic, natural-language search over the codebase — find code by what it DOES, "
        "not just literal text. action: search (default) | index (rebuild the embedding "
        "index). Give a query like 'where are auth tokens validated'. Falls back to the "
        "structural repo map when no embeddings provider is configured. Use this to locate "
        "relevant code on a large/unfamiliar repo before reading files."
    )
    groups = ["fs"]
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "index"], "default": "search"},
            "query": {"type": "string", "description": "Natural-language description of what to find."},
            "path": {"type": "string", "description": "Directory to scope to (default: cwd)."},
            "k": {"type": "integer", "description": "Max results (default 8)."},
        },
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        from .. import repomap, semantic_index

        root = Path(args.get("path") or ".").expanduser()
        if not root.is_absolute():
            root = (ctx.cwd or Path.cwd()) / root
        root = root if root.is_dir() else root.parent
        if not root.exists():
            return ToolResult.error(f"no such path: {root}")
        config = ctx.config
        action = args.get("action") or "search"

        if action == "index":
            if not semantic_index.embeddings_available(config):
                return ToolResult.error(
                    "no embeddings provider configured — set embeddings.api_key (or "
                    "OPENAI_API_KEY). code_search still works via the structural repo map.")
            res = semantic_index.build(root, config, force=True)
            return ToolResult.ok(f"indexed {res.get('indexed', 0)} file(s), "
                                 f"{res.get('chunks', 0)} chunk(s).", display="code_search: reindexed")

        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult.error("search requires a query")
        k = int(args.get("k") or 8)

        if semantic_index.embeddings_available(config):
            try:
                hits = semantic_index.search(root, query, config, k=k)
            except Exception as e:  # noqa: BLE001
                hits = []
                fallback_note = f"(semantic search failed: {e}; showing structural map)\n"
            else:
                fallback_note = ""
            if hits:
                body = "\n\n".join(
                    f"{h['path']}:{h['start']}-{h['end']}  (score {h['score']})\n{h['snippet']}"
                    for h in hits)
                return ToolResult.ok(body, display=f"code_search: {len(hits)} semantic hit(s)")
        else:
            fallback_note = "(no embeddings provider — structural repo map; set embeddings.api_key for semantic search)\n"

        # Fallback: the structural repo map (a natural-language query can't substring-match
        # symbol names, so show the whole ranked map rather than an empty filtered one).
        mapped = repomap.render_map(root)
        return ToolResult.ok(fallback_note + mapped, display="code_search: repo map (fallback)")


def code_search_tools() -> list[Tool]:
    return [CodeSearchTool()]
