"""Drop-in plugin loader.

Place ``*.py`` files under ``~/.aegis/plugins/`` that define ``register(api)``.
The ``api`` object exposes ``register_tool(tool)``, ``register_channel(name, factory)``,
and ``register_provider(spec)`` so plugins extend AEGIS without core edits.

Example ``~/.aegis/plugins/hello_tool.py``::

    from aegis.tools.base import Tool, ToolResult
    class Hello(Tool):
        name = "hello"; description = "say hi"
        parameters = {"type": "object", "properties": {}}
        def run(self, args, ctx): return ToolResult.ok("hi!")
    def register(api): api.register_tool(Hello())
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from . import config as cfg


class PluginAPI:
    def __init__(self):
        self.tools: list = []
        self.channels: dict = {}

    def register_tool(self, tool) -> None:
        self.tools.append(tool)

    def register_channel(self, name: str, factory) -> None:
        self.channels[name] = factory

    def register_provider(self, spec) -> None:
        from .providers.registry import register_provider
        register_provider(spec)


def load_plugins() -> PluginAPI:
    api = PluginAPI()
    base = cfg.sub("plugins")
    if not base.exists():
        return api
    for f in sorted(base.rglob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"aegis_plugin_{f.stem}", f)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            if hasattr(module, "register"):
                module.register(api)
        except Exception as e:  # noqa: BLE001
            print(f"  ! plugin {f.name} failed to load: {e}")
    return api
