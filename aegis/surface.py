"""Inventory helpers for tools, skills, and first-run visibility."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass
class ToolInventory:
    toolsets: list[str]
    enabled_count: int
    total_count: int
    enabled_names: list[str]
    disabled_sets: dict[str, int]


@dataclass
class SkillInventory:
    available_count: int
    bundled_count: int
    personal_count: int
    names: list[str]


@dataclass
class PluginInventory:
    path: Path
    files_count: int
    loaded_files: list[str]
    errors: list[tuple[str, str]]
    tools: list[str]
    channels: list[str]
    providers: list[str]


def tool_inventory(config: Config) -> ToolInventory:
    from .tools.registry import default_registry

    reg = default_registry()
    toolsets = list(config.get("tools.toolsets", []) or ["core"])
    enabled = reg.available(toolsets)
    enabled_ids = {id(t) for t in enabled}
    disabled_sets: dict[str, int] = {}
    for tool in reg.all():
        if id(tool) in enabled_ids:
            continue
        disabled_sets[tool.toolset] = disabled_sets.get(tool.toolset, 0) + 1
    return ToolInventory(
        toolsets=toolsets,
        enabled_count=len(enabled),
        total_count=len(reg.all()),
        enabled_names=sorted(t.name for t in enabled),
        disabled_sets=disabled_sets,
    )


def skill_inventory(config: Config, cwd: Path | None = None) -> SkillInventory:
    from .skills import SkillsLoader

    skills = SkillsLoader(config, cwd=cwd).available()
    return SkillInventory(
        available_count=len(skills),
        bundled_count=sum(1 for s in skills if s.tier == 4),
        personal_count=sum(1 for s in skills if s.tier < 4),
        names=sorted(s.name for s in skills),
    )


def plugin_inventory() -> PluginInventory:
    from . import config as cfg
    from .plugins import load_plugins

    api = load_plugins(quiet=True)
    return PluginInventory(
        path=cfg.sub("plugins"),
        files_count=len(api.files),
        loaded_files=[str(p) for p in api.files if p not in {e[0] for e in api.errors}],
        errors=[(str(p), msg) for p, msg in api.errors],
        tools=sorted(getattr(t, "name", str(t)) for t in api.tools),
        channels=sorted(api.channels),
        providers=sorted(api.providers),
    )
