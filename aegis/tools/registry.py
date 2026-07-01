"""Tool registry: registration, toolset gating, schema generation."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from typing import Any

from ..types import ToolSchema
from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

CORE_TOOLSET = "core"
ALL_TOOLSET_ALIASES = frozenset({"all", "*"})
DEFAULT_TOOLSET_ALIASES: dict[str, tuple[str, ...]] = {
    "default": (CORE_TOOLSET,),
    "guided/default": (CORE_TOOLSET,),
}
HERMES_CORE_BUNDLE_TOOLSETS = (
    CORE_TOOLSET,
    "web",
    "vision",
    "browser",
    "voice",
    "computer",
    "homeassistant",
)
HERMES_DISTRIBUTION_ALIASES: dict[str, tuple[str, ...]] = {
    "image_gen": ("image_gen", "vision", "web", "terminal"),
    "research": ("web", "browser", "vision", "terminal"),
    "science": ("web", "terminal", "file", "vision", "browser", "image_gen"),
    "development": ("terminal", "file", "web", "vision"),
    # Hermes safe/browser-only profiles intentionally avoid AEGIS core because
    # core currently also carries terminal and file tools.
    "safe": ("web", "browser", "vision", "image_gen"),
    "balanced": ("web", "vision", "image_gen", "terminal", "file", "browser"),
    "minimal": ("web",),
    "terminal_only": ("terminal", "file"),
    "terminal_web": ("terminal", "file", "web"),
    "creative": ("image_gen", "vision", "web"),
    "reasoning": ("web", "file", "terminal"),
    "browser_use": ("browser", "web", "vision"),
    "browser_only": ("browser",),
    "browser_tasks": ("browser", "vision", "terminal"),
    "terminal_tasks": ("terminal", "file", "web", "browser", "vision", "image_gen"),
    "mixed_tasks": ("browser", "terminal", "file", "web", "vision", "image_gen"),
}
HERMES_TOOLSET_DISTRIBUTIONS: dict[str, dict[str, Any]] = {
    "default": {
        "description": "All available tools, all the time",
        "toolsets": {
            "web": 100,
            "vision": 100,
            "image_gen": 100,
            "terminal": 100,
            "file": 100,
            "browser": 100,
        },
    },
    "image_gen": {
        "description": "Heavy focus on image generation with vision and web support",
        "toolsets": {"image_gen": 90, "vision": 90, "web": 55, "terminal": 45},
    },
    "research": {
        "description": "Web research with vision analysis and reasoning",
        "toolsets": {"web": 90, "browser": 70, "vision": 50, "terminal": 10},
    },
    "science": {
        "description": "Scientific research with web, terminal, file, and browser capabilities",
        "toolsets": {
            "web": 94,
            "terminal": 94,
            "file": 94,
            "vision": 65,
            "browser": 50,
            "image_gen": 15,
        },
    },
    "development": {
        "description": "Terminal, file tools, and reasoning with occasional web lookup",
        "toolsets": {"terminal": 80, "file": 80, "web": 30, "vision": 10},
    },
    "safe": {
        "description": "All tools except terminal for safety",
        "toolsets": {"web": 80, "browser": 70, "vision": 60, "image_gen": 60},
    },
    "balanced": {
        "description": "Equal probability of all toolsets",
        "toolsets": {
            "web": 50,
            "vision": 50,
            "image_gen": 50,
            "terminal": 50,
            "file": 50,
            "browser": 50,
        },
    },
    "minimal": {
        "description": "Only web tools for basic research",
        "toolsets": {"web": 100},
    },
    "terminal_only": {
        "description": "Terminal and file tools for code execution tasks",
        "toolsets": {"terminal": 100, "file": 100},
    },
    "terminal_web": {
        "description": "Terminal and file tools with web search for documentation lookup",
        "toolsets": {"terminal": 100, "file": 100, "web": 100},
    },
    "creative": {
        "description": "Image generation and vision analysis focus",
        "toolsets": {"image_gen": 90, "vision": 90, "web": 30},
    },
    "reasoning": {
        "description": "Heavy research/reasoning distribution with minimal other tools",
        "toolsets": {"web": 90, "file": 60, "terminal": 20},
    },
    "browser_use": {
        "description": "Full browser-based web interaction with search, vision, and page control",
        "toolsets": {"browser": 100, "web": 80, "vision": 70},
    },
    "browser_only": {
        "description": "Only browser automation tools for pure web interaction tasks",
        "toolsets": {"browser": 100},
    },
    "browser_tasks": {
        "description": (
            "Browser-focused distribution (browser toolset includes web_search for finding URLs "
            "since Google blocks direct browser searches)"
        ),
        "toolsets": {"browser": 97, "vision": 12, "terminal": 15},
    },
    "terminal_tasks": {
        "description": (
            "Terminal-focused distribution with high terminal/file availability, "
            "occasional other tools"
        ),
        "toolsets": {
            "terminal": 97,
            "file": 97,
            "web": 97,
            "browser": 75,
            "vision": 50,
            "image_gen": 10,
        },
    },
    "mixed_tasks": {
        "description": (
            "Mixed distribution with high browser, terminal, and file availability "
            "for complex tasks"
        ),
        "toolsets": {
            "browser": 92,
            "terminal": 92,
            "file": 92,
            "web": 35,
            "vision": 15,
            "image_gen": 15,
        },
    },
}
HERMES_PLATFORM_TOOLSET_ALIASES: dict[str, tuple[str, ...]] = {
    "hermes-acp": (CORE_TOOLSET, "web", "vision", "browser"),
    "hermes-api-server": (CORE_TOOLSET, "web", "vision", "browser", "homeassistant"),
    "hermes-cli": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-cron": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-telegram": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-discord": (*HERMES_CORE_BUNDLE_TOOLSETS, "discord", "discord_admin"),
    "hermes-whatsapp": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-slack": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-signal": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-bluebubbles": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-homeassistant": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-email": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-mattermost": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-matrix": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-dingtalk": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-feishu": (*HERMES_CORE_BUNDLE_TOOLSETS, "feishu_doc", "feishu_drive"),
    "hermes-weixin": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-qqbot": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-wecom": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-wecom-callback": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-yuanbao": (*HERMES_CORE_BUNDLE_TOOLSETS, "yuanbao"),
    "hermes-sms": HERMES_CORE_BUNDLE_TOOLSETS,
    "hermes-webhook": ("web", "vision", "clarify"),
    "hermes-gateway": (
        "hermes-telegram",
        "hermes-discord",
        "hermes-whatsapp",
        "hermes-slack",
        "hermes-signal",
        "hermes-bluebubbles",
        "hermes-homeassistant",
        "hermes-email",
        "hermes-sms",
        "hermes-mattermost",
        "hermes-matrix",
        "hermes-dingtalk",
        "hermes-feishu",
        "hermes-wecom",
        "hermes-wecom-callback",
        "hermes-weixin",
        "hermes-qqbot",
        "hermes-webhook",
        "hermes-yuanbao",
    ),
}
HERMES_STATIC_LEAF_TOOLSETS = frozenset({
    "web",
    "search",
    "x_search",
    "vision",
    "video",
    "image_gen",
    "video_gen",
    "computer_use",
    "computer",
    "terminal",
    "skills",
    "browser",
    "cronjob",
    "file",
    "tts",
    "voice",
    "todo",
    "memory",
    "context_engine",
    "session_search",
    "project",
    "clarify",
    "code_execution",
    "delegation",
    "homeassistant",
    "kanban",
    "discord",
    "discord_admin",
    "yuanbao",
    "feishu_doc",
    "feishu_drive",
    "spotify",
})
STATIC_TOOLSET_ALIASES: dict[str, tuple[str, ...]] = {
    **DEFAULT_TOOLSET_ALIASES,
    **HERMES_DISTRIBUTION_ALIASES,
    **HERMES_PLATFORM_TOOLSET_ALIASES,
}
CORE_PROTECTED_TOOLSETS = frozenset({CORE_TOOLSET})
STATIC_RESERVED_TOOLSET_NAMES = (
    frozenset(STATIC_TOOLSET_ALIASES)
    | ALL_TOOLSET_ALIASES
    | CORE_PROTECTED_TOOLSETS
)


def _clean_toolset_names(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)
        except TypeError:
            items = [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            clean = item.strip()
            if clean and clean not in out:
                out.append(clean)
    return out


def _clean_toolset_aliases(value: dict[str, str] | None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not value:
        return aliases
    for alias, target in value.items():
        if not isinstance(alias, str) or not isinstance(target, str):
            continue
        clean_alias = alias.strip()
        clean_target = target.strip()
        if clean_alias and clean_target:
            aliases[clean_alias] = clean_target
    return aliases


def _display_toolset_names(names: list[str], aliases: dict[str, str]) -> list[str]:
    canonical = _clean_toolset_names(names)
    canonical_set = set(canonical)
    reserved = set(STATIC_RESERVED_TOOLSET_NAMES) | canonical_set
    reverse_aliases: dict[str, str] = {}
    for alias, target in _clean_toolset_aliases(aliases).items():
        if alias in reserved or target not in canonical_set:
            continue
        reverse_aliases.setdefault(target, alias)

    out: list[str] = []
    for name in canonical:
        display = reverse_aliases.get(name, name)
        if display not in out:
            out.append(display)
    return out


def _tool_extra_toolsets(tool: Tool) -> list[str]:
    return _clean_toolset_names(
        getattr(tool, "extra_toolsets", None)
        or getattr(tool, "additional_toolsets", None)
    )


def _tool_toolsets(tool: Tool) -> list[str]:
    primary = str(getattr(tool, "toolset", "") or CORE_TOOLSET).strip() or CORE_TOOLSET
    out = [primary]
    for name in _tool_extra_toolsets(tool):
        if name not in out:
            out.append(name)
    return out


def get_toolset_distribution(name: str) -> dict[str, Any] | None:
    return HERMES_TOOLSET_DISTRIBUTIONS.get(str(name or ""))


def get_distribution(name: str) -> dict[str, Any] | None:
    """Hermes-compatible alias for ``get_toolset_distribution``."""
    return get_toolset_distribution(name)


def list_toolset_distributions() -> dict[str, dict[str, Any]]:
    return HERMES_TOOLSET_DISTRIBUTIONS.copy()


def list_distributions() -> dict[str, dict[str, Any]]:
    """Hermes-compatible alias for ``list_toolset_distributions``."""
    return list_toolset_distributions()


def validate_toolset_name(
    name: str,
    *,
    known_toolsets: list[str] | set[str] | tuple[str, ...] | None = None,
    toolset_aliases: dict[str, str] | None = None,
) -> bool:
    clean = str(name or "").strip()
    if not clean:
        return False
    if clean in ALL_TOOLSET_ALIASES or clean in HERMES_STATIC_LEAF_TOOLSETS:
        return True
    if clean in STATIC_TOOLSET_ALIASES:
        return True
    known = set(_clean_toolset_names(known_toolsets))
    if clean in known or clean in _clean_toolset_aliases(toolset_aliases):
        return True
    if clean.startswith("hermes-") and clean[len("hermes-"):] in known:
        return True
    return False


def validate_distribution(distribution_name: str) -> bool:
    return str(distribution_name or "") in HERMES_TOOLSET_DISTRIBUTIONS


def validate_toolset_distribution(distribution_name: str) -> bool:
    return validate_distribution(distribution_name)


def sample_toolsets_from_distribution(
    distribution_name: str,
    *,
    known_toolsets: list[str] | set[str] | tuple[str, ...] | None = None,
    toolset_aliases: dict[str, str] | None = None,
    rng: Any = None,
) -> list[str]:
    dist = get_toolset_distribution(distribution_name)
    if not dist:
        raise ValueError(f"Unknown distribution: {distribution_name}")
    random_source = rng or random
    selected: list[str] = []
    for toolset_name, probability in dist["toolsets"].items():
        if not validate_toolset_name(
            toolset_name,
            known_toolsets=known_toolsets,
            toolset_aliases=toolset_aliases,
        ):
            continue
        try:
            threshold = float(probability)
        except (TypeError, ValueError):
            continue
        if float(random_source.random()) * 100 < threshold:
            selected.append(toolset_name)
    if not selected and dist["toolsets"]:
        highest = max(dist["toolsets"].items(), key=lambda item: item[1])[0]
        if validate_toolset_name(
            highest,
            known_toolsets=known_toolsets,
            toolset_aliases=toolset_aliases,
        ):
            selected.append(highest)
    return selected


def print_distribution_info(distribution_name: str) -> None:
    dist = get_toolset_distribution(distribution_name)
    if not dist:
        print(f"Unknown distribution: {distribution_name}")
        return
    print(f"\nDistribution: {distribution_name}")
    print(f"   Description: {dist['description']}")
    print("   Toolsets:")
    for toolset, probability in sorted(
        dist["toolsets"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(f"     - {toolset:15} : {probability:3}% chance")


def resolve_toolset_names(
    toolsets: list[str] | set[str] | tuple[str, ...] | str | None,
    *,
    known_toolsets: list[str] | set[str] | tuple[str, ...] | None = None,
    toolset_aliases: dict[str, str] | None = None,
) -> list[str]:
    """Resolve AEGIS toolset aliases to concrete toolset names.

    ``default`` and ``guided/default`` are compatibility aliases for the core
    AEGIS toolset. Hermes distribution and platform bundle names expand through
    the static include graph above. Registry aliases let plugin/display names
    resolve to canonical dynamic toolsets. The legacy ``mcp`` selector expands
    to all dynamic ``mcp-*`` server toolsets while keeping a concrete ``mcp``
    toolset visible if one is registered. ``all`` and ``*`` expand to every
    known registered toolset while always keeping ``core`` in the expansion so
    dynamic/plugin toolsets do not accidentally replace the control surface.
    """
    known = set(_clean_toolset_names(known_toolsets))
    known.update(CORE_PROTECTED_TOOLSETS)
    aliases = _clean_toolset_aliases(toolset_aliases)
    all_names = [CORE_TOOLSET, *sorted(name for name in known if name != CORE_TOOLSET)]

    requested = _clean_toolset_names(toolsets) or [CORE_TOOLSET]
    resolved: list[str] = []

    def add(name: str) -> None:
        if name and name not in resolved:
            resolved.append(name)

    visiting: set[str] = set()

    def expand(name: str, *, nested: bool = False) -> None:
        if name in ALL_TOOLSET_ALIASES:
            for known_name in all_names:
                add(known_name)
            return
        if name == "mcp":
            mcp_toolsets = sorted(
                known_name
                for known_name in known
                if known_name == "mcp" or known_name.startswith("mcp-")
            )
            if mcp_toolsets:
                for known_name in mcp_toolsets:
                    add(known_name)
                return
        if nested and name in known:
            add(name)
            return
        if name in visiting:
            return
        expanded = STATIC_TOOLSET_ALIASES.get(name)
        if expanded is not None:
            visiting.add(name)
            for candidate in expanded:
                expand(candidate, nested=True)
            visiting.remove(name)
            return
        if name.startswith("hermes-"):
            platform_name = name[len("hermes-"):]
            platform_target = aliases.get(platform_name, platform_name)
            if platform_target in known:
                visiting.add(name)
                for candidate in (*HERMES_CORE_BUNDLE_TOOLSETS, platform_target):
                    expand(candidate, nested=True)
                visiting.remove(name)
                return
        if name in known:
            add(name)
            return
        alias_target = aliases.get(name)
        if alias_target:
            visiting.add(name)
            expand(alias_target)
            visiting.remove(name)
            return
        add(name)

    for name in requested:
        expand(name)
    return resolved or [CORE_TOOLSET]


def resolve_disabled_toolset_names(
    toolsets: list[str] | set[str] | tuple[str, ...] | str | None,
    *,
    known_toolsets: list[str] | set[str] | tuple[str, ...] | None = None,
    toolset_aliases: dict[str, str] | None = None,
) -> list[str]:
    """Resolve disabled toolsets, preserving shared AEGIS core for Hermes bundles."""
    aliases = _clean_toolset_aliases(toolset_aliases)
    out: list[str] = []

    def add(name: str) -> None:
        if name and name not in out:
            out.append(name)

    for name in _clean_toolset_names(toolsets):
        resolved = resolve_toolset_names(
            [name],
            known_toolsets=known_toolsets,
            toolset_aliases=aliases,
        )
        canonical_name = aliases.get(name, name)
        if canonical_name.startswith("hermes-"):
            for resolved_name in resolved:
                if resolved_name not in HERMES_CORE_BUNDLE_TOOLSETS:
                    add(resolved_name)
            continue
        for resolved_name in resolved:
            add(resolved_name)
    return out


class ToolAlias(Tool):
    """compatibility tool name that delegates to an existing AEGIS tool."""

    source = "alias"
    manifest_id = "aegis-compat"

    def __init__(
        self,
        name: str,
        target: Tool,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.target = target
        self.description = description or f"Compatibility alias for `{target.name}`."
        self.parameters = parameters if parameters is not None else dict(target.parameters)
        self.transform = transform or (lambda args: dict(args or {}))
        self.groups = list(getattr(target, "groups", []) or [])
        self.toolset = str(getattr(target, "toolset", "") or "core")
        self.extra_toolsets = _tool_extra_toolsets(target)
        self.source_path = f"alias://{name}->{target.name}"
        self.required_env = list(getattr(target, "required_env", []) or [])
        self.required_auth = list(getattr(target, "required_auth", []) or [])
        self.output_limits = dict(getattr(target, "output_limits", {}) or {})
        self.max_result_size_chars = getattr(target, "max_result_size_chars", None)
        self.risk_level = str(getattr(target, "risk_level", "") or "")

    def available(self) -> tuple[bool, str]:
        return self.target.available()

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.target.run(self.transform(dict(args or {})), ctx)


class ToolRegistry:
    def __init__(self, *, enforce_schema: bool = False):
        self._tools: dict[str, Tool] = {}
        self.enforce_schema = enforce_schema
        self._rejections: list[dict[str, object]] = []
        self._toolset_aliases: dict[str, str] = {}
        self._generation = 0

    def _bump_generation(self) -> None:
        self._generation += 1

    def _reject(self, tool: Tool, reason: str, *, issues: list[dict] | None = None) -> None:
        record = {
            "tool": str(getattr(tool, "name", "") or "<unnamed>"),
            "source": str(getattr(tool, "source", "") or getattr(tool, "toolset", "") or "tool"),
            "toolset": str(getattr(tool, "toolset", "") or "core"),
            "reason": reason,
            "issues": issues or [],
        }
        self._rejections.append(record)
        logger.warning("Tool registration rejected: %s (%s)", record["tool"], reason)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        if self.enforce_schema:
            from .schema_validation import validate_tool_schema

            issues = validate_tool_schema(tool)
            errors = [issue for issue in issues if issue.severity == "error"]
            if errors:
                self._reject(
                    tool,
                    "invalid schema",
                    issues=[issue.to_dict() for issue in issues],
                )
                return
        existing = self._tools.get(tool.name)
        if existing is not None:
            allow_shadow = bool(getattr(tool, "allow_shadow", False))
            if not allow_shadow:
                existing_source = str(getattr(existing, "source", "") or "")
                self._reject(
                    tool,
                    (
                        f"duplicate name shadows existing {existing_source or 'tool'} "
                        f"from toolset '{existing.toolset}'"
                    ),
                )
                return
        self._tools[tool.name] = tool
        alias = str(getattr(tool, "toolset_alias", "") or "").strip()
        toolset = str(getattr(tool, "toolset", "") or CORE_TOOLSET).strip() or CORE_TOOLSET
        if alias:
            self.register_toolset_alias(alias, toolset)
        self._bump_generation()

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def deregister(self, name: str) -> Tool | None:
        """Remove a tool by name, returning the removed tool if present."""
        removed = self._tools.pop(str(name or ""), None)
        if removed is not None:
            removed_toolsets = set(_tool_toolsets(removed))
            live_toolsets = {
                toolset
                for tool in self._tools.values()
                for toolset in _tool_toolsets(tool)
            }
            stale_toolsets = removed_toolsets - live_toolsets
            if stale_toolsets:
                self._toolset_aliases = {
                    alias: target
                    for alias, target in self._toolset_aliases.items()
                    if target not in stale_toolsets
                }
            self._bump_generation()
        return removed

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def _canonical_toolset_names(self) -> list[str]:
        names = {CORE_TOOLSET}
        for tool in self._tools.values():
            for name in _tool_toolsets(tool):
                names.add(name)
        return [CORE_TOOLSET, *sorted(name for name in names if name != CORE_TOOLSET)]

    def toolset_names(self, *, display_aliases: bool = True) -> list[str]:
        names = self._canonical_toolset_names()
        if not display_aliases:
            return names
        return _display_toolset_names(names, self._toolset_aliases)

    def register_toolset_alias(self, alias: str, toolset: str) -> None:
        clean_alias = str(alias or "").strip()
        clean_toolset = str(toolset or "").strip()
        if not clean_alias or not clean_toolset:
            return
        existing = self._toolset_aliases.get(clean_alias)
        if existing == clean_toolset:
            return
        if existing and existing != clean_toolset:
            logger.warning(
                "Toolset alias collision: %r (%s) overwritten by %s",
                clean_alias,
                existing,
                clean_toolset,
            )
        self._toolset_aliases[clean_alias] = clean_toolset
        self._bump_generation()

    def get_registered_toolset_aliases(self) -> dict[str, str]:
        return dict(self._toolset_aliases)

    def get_toolset_alias_target(self, alias: str) -> str | None:
        return self._toolset_aliases.get(str(alias or "").strip())

    def get_tool_names_for_toolset(self, toolset: str) -> list[str]:
        canonical = set(self.resolve_toolsets([toolset]))
        return sorted(
            tool.name
            for tool in self._tools.values()
            if set(_tool_toolsets(tool)) & canonical
        )

    def resolve_toolsets(
        self,
        toolsets: list[str] | set[str] | tuple[str, ...] | str | None,
    ) -> list[str]:
        return resolve_toolset_names(
            toolsets,
            known_toolsets=self._canonical_toolset_names(),
            toolset_aliases=self._toolset_aliases,
        )

    def resolve_disabled_toolsets(
        self,
        toolsets: list[str] | set[str] | tuple[str, ...] | str | None,
    ) -> list[str]:
        return resolve_disabled_toolset_names(
            toolsets,
            known_toolsets=self._canonical_toolset_names(),
            toolset_aliases=self._toolset_aliases,
        )

    def rejections(self) -> list[dict[str, object]]:
        return list(self._rejections)

    def available(
        self,
        toolsets: list[str],
        *,
        only_usable: bool = True,
        disabled: list[str] | set[str] | None = None,
        disabled_toolsets: list[str] | set[str] | tuple[str, ...] | str | None = None,
    ) -> list[Tool]:
        """Tools in the enabled toolsets.

        Toolset aliases are resolved before filtering: empty/default-like input
        means ``core``, while ``all``/``*`` expands to all registered toolsets.
        With ``only_usable`` (default) this also drops tools whose environment
        deps are missing, so the model never sees a tool it can't run.
        ``disabled`` is a per-tool denylist (config ``tools.disabled``) that
        hides individual tools even when their toolset is active — the
        dashboard's per-tool on/off switch. ``disabled_toolsets`` subtracts
        whole toolsets, while Hermes platform bundles remove only their
        platform-specific deltas so shared AEGIS core stays available.
        """
        enabled = set(self.resolve_toolsets(toolsets))
        enabled.difference_update(self.resolve_disabled_toolsets(disabled_toolsets))
        deny = set(disabled or ())
        out = []
        for t in self._tools.values():
            if t.name in deny:
                continue
            target = getattr(t, "target", None)
            if target is not None and getattr(target, "name", None) in deny:
                continue
            if not (set(_tool_toolsets(t)) & enabled):
                continue
            if only_usable and not t.available()[0]:
                continue
            out.append(t)
        return out

    def schemas(self, tools: list[Tool]) -> list[ToolSchema]:
        return [t.schema() for t in tools]

    def get_max_result_size(self, name: str, default: int | float | None = None) -> int | float:
        """Return the registered per-result persistence cap for a tool."""
        fallback = 100_000 if default is None else default
        tool = self.get(str(name or ""))
        if tool is None:
            return fallback
        value = getattr(tool, "max_result_size_chars", None)
        if value is None:
            limits = getattr(tool, "output_limits", None)
            if isinstance(limits, dict):
                value = (
                    limits.get("max_result_size_chars")
                    or limits.get("max_result_chars")
                    or limits.get("result_max_chars")
                )
        if value is None:
            target = getattr(tool, "target", None)
            value = getattr(target, "max_result_size_chars", None)
        if value is None:
            return fallback
        if value == float("inf"):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties or {})}
    if required:
        schema["required"] = list(required)
    return schema


def _with_action(action: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def transform(args: dict[str, Any]) -> dict[str, Any]:
        out = dict(args)
        out["action"] = action
        return out

    return transform


def _register_alias(reg: ToolRegistry, alias: str, target: str, **kwargs: Any) -> None:
    tool = reg.get(target)
    if tool is None:
        return
    reg.register(ToolAlias(alias, tool, **kwargs))


def _register_aegis_aliases(reg: ToolRegistry) -> None:
    """Expose familiar agent tool names without duplicating tool logic."""
    direct = {
        "terminal": "bash",
        "patch": "apply_patch",
        "search_files": "search",
        "x_search": "web_search",
        "delegate_task": "spawn_subagent",
        "todo": "todo_write",
        "image_generate": "generate_image",
        "text_to_speech": "speak",
        "speech_to_text": "transcribe",
        "audio_transcribe": "transcribe",
        "computer_use": "computer",
    }
    for alias, target in direct.items():
        _register_alias(reg, alias, target)

    _register_alias(
        reg,
        "audio_analyze",
        "media_analyze",
        description="Analyze an audio file by transcribing it with the configured STT provider.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "description": "Local audio file path."},
                "prompt": {"type": "string"},
                "model": {"type": "string"},
            },
            required=["path"],
        ),
        transform=lambda args: {**args, "media_type": "audio"},
    )
    _register_alias(
        reg,
        "video_analyze",
        "media_analyze",
        description="Analyze a video by sampling frames with ffmpeg and using the vision model.",
        parameters=_object_schema(
            {
                "path": {"type": "string", "description": "Local video file path."},
                "prompt": {"type": "string"},
                "max_frames": {"type": "integer"},
            },
            required=["path"],
        ),
        transform=lambda args: {**args, "media_type": "video"},
    )

    _register_alias(
        reg,
        "read_terminal",
        "process",
        description="Read output from a long-running terminal/process session.",
        parameters=_object_schema(
            {
                "id": {"type": "string", "description": "Process/session id."},
                "session_id": {"type": "string", "description": "Process/session id alias."},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            }
        ),
        transform=_with_action("logs"),
    )

    _register_alias(
        reg,
        "skills_list",
        "skill",
        description="List available skills.",
        parameters=_object_schema(),
        transform=lambda _args: {"action": "list"},
    )
    _register_alias(
        reg,
        "skill_view",
        "skill",
        description="Load the full body for one skill.",
        parameters=_object_schema(
            {
                "name": {"type": "string", "description": "Skill name."},
                "skill": {"type": "string", "description": "Skill name alias."},
            }
        ),
        transform=lambda args: {"action": "view", "name": args.get("name") or args.get("skill") or ""},
    )

    browser_actions = {
        "browser_navigate": (
            "navigate",
            {"url": {"type": "string"}},
            ["url"],
            "Navigate the browser to a URL.",
        ),
        "browser_open": (
            "navigate",
            {"url": {"type": "string"}},
            ["url"],
            "Open a URL in the browser.",
        ),
        "browser_goto": (
            "navigate",
            {"url": {"type": "string"}},
            ["url"],
            "Go to a URL in the browser.",
        ),
        "browser_click": (
            "click",
            {"selector": {"type": "string"}},
            ["selector"],
            "Click an element by selector.",
        ),
        "browser_type": (
            "type",
            {"selector": {"type": "string"}, "text": {"type": "string"}},
            ["selector", "text"],
            "Type text into an element by selector.",
        ),
        "browser_fill": (
            "type",
            {"selector": {"type": "string"}, "text": {"type": "string"}},
            ["selector", "text"],
            "Fill an element by selector.",
        ),
        "browser_text": ("text", {}, [], "Return readable page text."),
        "browser_read": ("text", {}, [], "Read readable page text."),
        "browser_get_text": ("text", {}, [], "Get readable page text."),
        "browser_snapshot": ("text", {}, [], "Return a compact textual browser snapshot."),
        "browser_html": ("html", {}, [], "Return page HTML."),
        "browser_content": ("html", {}, [], "Return page HTML content."),
        "browser_source": ("html", {}, [], "Return page source HTML."),
        "browser_screenshot": (
            "screenshot",
            {"path": {"type": "string"}},
            [],
            "Save a browser screenshot.",
        ),
        "browser_capture": (
            "screenshot",
            {"path": {"type": "string"}},
            [],
            "Capture a browser screenshot.",
        ),
        "browser_back": ("back", {}, [], "Go back in browser history."),
        "browser_go_back": ("back", {}, [], "Go back in browser history."),
        # AEGIS keeps scroll/key/dialog/CDP controls in the computer/devtools layer.
        # These aliases expose the familiar browser names and return the browser
        # state that is safe to collect without adding new destructive controls.
        "browser_scroll": ("text", {}, [], "Read page text after external/browser scrolling."),
        "browser_press": ("text", {}, [], "Read page text after external/browser key input."),
        "browser_console": ("html", {}, [], "Return page HTML for console/context inspection."),
        "browser_get_images": ("html", {}, [], "Return page HTML so image URLs can be extracted."),
        "browser_vision": ("screenshot", {"path": {"type": "string"}}, [], "Capture a screenshot for vision analysis."),
        "browser_cdp": ("html", {}, [], "Return page HTML from the connected browser context."),
        "browser_dialog": ("text", {}, [], "Return page text after dialog handling by the browser backend."),
    }
    for alias, (action, props, required, description) in browser_actions.items():
        _register_alias(
            reg,
            alias,
            "browser",
            description=description,
            parameters=_object_schema(props, required=required),
            transform=_with_action(action),
        )

    kanban_actions = {
        "kanban_list": (
            "list",
            {"filter_status": {"type": "string"}},
            [],
            "List kanban cards.",
        ),
        "kanban_create": (
            "create",
            {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "priority": {"type": "integer"},
                "assignee": {"type": "string"},
                "parents": {"type": "array", "items": {"type": "string"}},
                "tenant": {"type": "string"},
                "workspace": {"type": "string"},
            },
            ["title"],
            "Create a kanban card.",
        ),
        "kanban_add": (
            "create",
            {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "priority": {"type": "integer"},
                "assignee": {"type": "string"},
                "parents": {"type": "array", "items": {"type": "string"}},
                "tenant": {"type": "string"},
                "workspace": {"type": "string"},
            },
            ["title"],
            "Add a kanban card.",
        ),
        "kanban_show": ("show", {"id": {"type": "string"}}, ["id"], "Show a kanban card."),
        "kanban_get": ("show", {"id": {"type": "string"}}, ["id"], "Get a kanban card."),
        "kanban_view": ("show", {"id": {"type": "string"}}, ["id"], "View a kanban card."),
        "kanban_move": (
            "move",
            {"id": {"type": "string"}, "status": {"type": "string"}},
            ["id", "status"],
            "Move a kanban card to another status.",
        ),
        "kanban_update_status": (
            "move",
            {"id": {"type": "string"}, "status": {"type": "string"}},
            ["id", "status"],
            "Update a kanban card status.",
        ),
        "kanban_set_status": (
            "move",
            {"id": {"type": "string"}, "status": {"type": "string"}},
            ["id", "status"],
            "Set a kanban card status.",
        ),
        "kanban_complete": (
            "complete",
            {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "metadata": {"type": "object"},
                "created_cards": {"type": "array", "items": {"type": "string"}},
            },
            ["id"],
            "Mark a kanban card complete.",
        ),
        "kanban_done": (
            "complete",
            {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "metadata": {"type": "object"},
                "created_cards": {"type": "array", "items": {"type": "string"}},
            },
            ["id"],
            "Mark a kanban card done.",
        ),
        "kanban_finish": (
            "complete",
            {
                "id": {"type": "string"},
                "text": {"type": "string"},
                "metadata": {"type": "object"},
                "created_cards": {"type": "array", "items": {"type": "string"}},
            },
            ["id"],
            "Finish a kanban card.",
        ),
        "kanban_block": (
            "block",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id"],
            "Block a kanban card.",
        ),
        "kanban_unblock": ("unblock", {"id": {"type": "string"}}, ["id"], "Unblock a kanban card."),
        "kanban_comment": (
            "comment",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id", "text"],
            "Comment on a kanban card.",
        ),
        "kanban_note": (
            "comment",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id", "text"],
            "Add a note to a kanban card.",
        ),
        "kanban_heartbeat": (
            "heartbeat",
            {"id": {"type": "string"}, "text": {"type": "string"}},
            ["id"],
            "Record kanban worker heartbeat.",
        ),
        "kanban_link": (
            "link",
            {"parent": {"type": "string"}, "child": {"type": "string"}},
            ["parent", "child"],
            "Link a parent and child kanban card.",
        ),
        "kanban_depend": (
            "link",
            {"parent": {"type": "string"}, "child": {"type": "string"}},
            ["parent", "child"],
            "Link a dependency between kanban cards.",
        ),
        "kanban_runs": ("runs", {"id": {"type": "string"}}, ["id"], "List runs for a kanban card."),
    }
    for alias, (action, props, required, description) in kanban_actions.items():
        _register_alias(
            reg,
            alias,
            "kanban",
            description=description,
            parameters=_object_schema(props, required=required),
            transform=_with_action(action),
        )


def default_registry(*, include_plugins: bool = True) -> ToolRegistry:
    """Registry pre-loaded with all built-in tools (+ extended + plugin tools)."""
    from .agentic import agentic_tools
    from .aux_tools import aux_tools
    from .browser import browser_tools
    from .ui_verify import web_verify_tools
    from .builtin import all_builtin_tools
    from .code_exec import code_tools
    from .extra_builtin import extra_tools
    from .cloud import cloud_tools
    from .devtools import dev_tools
    from .integrations import integration_tools
    from .lsp import lsp_tools
    from .process import process_tools
    from .project_tools import project_tools
    from .kanban_tool import kanban_tools
    from .code_search_tool import code_search_tools
    from .recall import recall_tools
    from .repomap_tool import repomap_tools
    from .skill_manage import skill_manage_tools
    from .state import state_tools
    from .voice import voice_tools

    reg = ToolRegistry(enforce_schema=True)
    reg.register_all(all_builtin_tools())
    reg.register_all(extra_tools())
    reg.register_all(aux_tools())
    reg.register_all(agentic_tools())
    reg.register_all(code_tools())
    reg.register_all(browser_tools())
    reg.register_all(web_verify_tools())
    reg.register_all(voice_tools())
    reg.register_all(lsp_tools())
    reg.register_all(recall_tools())
    reg.register_all(repomap_tools())
    reg.register_all(code_search_tools())
    reg.register_all(skill_manage_tools())
    reg.register_all(kanban_tools())
    reg.register_all(state_tools())
    reg.register_all(process_tools())
    reg.register_all(project_tools())
    reg.register_all(dev_tools())
    reg.register_all(cloud_tools())
    reg.register_all(integration_tools())
    _register_aegis_aliases(reg)
    if include_plugins:
        try:
            from ..plugins import load_plugins
            reg.register_all(load_plugins(quiet=True).tools)
        except Exception:  # noqa: BLE001
            pass
    return reg
