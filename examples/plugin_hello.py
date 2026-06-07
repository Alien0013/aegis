"""Example AEGIS plugin. Copy to ~/.aegis/plugins/ to load it.

Plugins expose a ``register(api)`` function. The api can register tools, channels,
or providers — no core edits needed.
"""

from aegis.tools.base import Tool, ToolResult


class HelloTool(Tool):
    name = "hello"
    description = "Say hello to someone. A minimal example tool."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def run(self, args, ctx) -> ToolResult:
        return ToolResult.ok(f"Hello, {args['name']}! 👋")


def register(api) -> None:
    api.register_tool(HelloTool())
