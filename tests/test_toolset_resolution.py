"""Registry-level toolset resolution parity checks."""

from __future__ import annotations

from typing import Any

from aegis.tools.base import Tool
from aegis.tools.registry import (
    ToolAlias,
    ToolRegistry,
    default_registry,
    get_distribution,
    list_distributions,
    print_distribution_info,
    sample_toolsets_from_distribution,
    validate_distribution,
    resolve_toolset_names,
)


class SyntheticTool(Tool):
    description = "Synthetic registry test tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, name: str, *, toolset: str = "core") -> None:
        self.name = name
        self.toolset = toolset


def _names(tools: list[Tool]) -> list[str]:
    return [tool.name for tool in tools]


def test_default_aliases_resolve_to_core_with_stable_dedupe() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_search", toolset="web"))

    assert reg.resolve_toolsets(["default", "guided/default", "web", "default"]) == [
        "core",
        "web",
    ]
    assert _names(reg.available(["default"], only_usable=False)) == ["core_read"]
    assert _names(reg.available(["guided/default"], only_usable=False)) == ["core_read"]


def test_all_and_star_expand_registered_dynamic_toolsets() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("plugin_action", toolset="plugin_z"))
    reg.register(SyntheticTool("mcp_action", toolset="mcp"))
    reg.register(SyntheticTool("core_read", toolset="core"))

    assert reg.toolset_names() == ["core", "mcp", "plugin_z"]
    assert reg.resolve_toolsets(["*"]) == ["core", "mcp", "plugin_z"]
    assert reg.resolve_toolsets(["all"]) == ["core", "mcp", "plugin_z"]
    assert set(_names(reg.available(["*"], only_usable=False))) == {
        "core_read",
        "mcp_action",
        "plugin_action",
    }
    assert set(_names(reg.available(["all"], only_usable=False))) == {
        "core_read",
        "mcp_action",
        "plugin_action",
    }


def test_plugin_toolset_display_alias_resolves_to_canonical_toolset() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("mcp_dynserver_ping", toolset="mcp-dynserver"))
    reg.register_toolset_alias("dynserver", "mcp-dynserver")

    assert reg.get_toolset_alias_target("dynserver") == "mcp-dynserver"
    assert reg.get_registered_toolset_aliases() == {"dynserver": "mcp-dynserver"}
    assert reg.toolset_names() == ["core", "dynserver"]
    assert reg.toolset_names(display_aliases=False) == ["core", "mcp-dynserver"]
    assert reg.get_tool_names_for_toolset("dynserver") == ["mcp_dynserver_ping"]
    assert reg.resolve_toolsets(["dynserver"]) == ["mcp-dynserver"]
    assert _names(reg.available(["dynserver"], only_usable=False)) == ["mcp_dynserver_ping"]
    assert _names(reg.available(["mcp-dynserver"], only_usable=False)) == [
        "mcp_dynserver_ping"
    ]


def test_plugin_toolset_alias_does_not_shadow_registered_toolset() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("web_search", toolset="web"))
    reg.register(SyntheticTool("mcp_web_search", toolset="mcp-web"))
    reg.register_toolset_alias("web", "mcp-web")

    assert reg.toolset_names() == ["core", "mcp-web", "web"]
    assert reg.resolve_toolsets(["web"]) == ["web"]
    assert _names(reg.available(["web"], only_usable=False)) == ["web_search"]


def test_plugin_toolset_alias_is_removed_with_last_tool() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("mcp_dynserver_ping", toolset="mcp-dynserver"))
    reg.register_toolset_alias("dynserver", "mcp-dynserver")

    reg.deregister("mcp_dynserver_ping")

    assert reg.get_toolset_alias_target("dynserver") is None
    assert reg.toolset_names() == ["core"]


def test_all_resolution_keeps_core_protected_without_registered_core_tools() -> None:
    assert resolve_toolset_names(["all"], known_toolsets=["mcp"]) == ["core", "mcp"]

    reg = ToolRegistry()
    reg.register(SyntheticTool("mcp_action", toolset="mcp"))

    assert reg.resolve_toolsets(["all"]) == ["core", "mcp"]
    assert _names(reg.available(["all"], only_usable=False)) == ["mcp_action"]


def test_disabled_tools_are_specific_after_alias_resolution() -> None:
    reg = ToolRegistry()
    bash = SyntheticTool("bash", toolset="core")
    reg.register(bash)
    reg.register(ToolAlias("terminal", bash))
    reg.register(SyntheticTool("mcp_action", toolset="mcp"))

    names = _names(reg.available(["all"], only_usable=False, disabled=["mcp_action"]))
    assert names == ["bash", "terminal"]

    names = _names(reg.available(["default"], only_usable=False, disabled=["bash"]))
    assert names == []


def test_disabled_toolsets_are_separate_from_per_tool_denylist() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_search", toolset="web"))

    assert _names(
        reg.available(
            ["default", "web"],
            only_usable=False,
            disabled=["core_read"],
        )
    ) == ["web_search"]
    assert _names(
        reg.available(
            ["default", "web"],
            only_usable=False,
            disabled_toolsets=["web"],
        )
    ) == ["core_read"]


def test_hermes_distribution_aliases_expand_to_aegis_toolsets() -> None:
    assert resolve_toolset_names(["terminal_web"], known_toolsets=["web"]) == [
        "terminal",
        "file",
        "web",
    ]
    assert resolve_toolset_names(
        ["browser_use"],
        known_toolsets=["browser", "web", "vision"],
    ) == [
        "browser",
        "web",
        "vision",
    ]


def test_hermes_minimal_distribution_alias_is_exact_web_only() -> None:
    assert resolve_toolset_names(
        ["minimal"],
        known_toolsets=["browser", "image_gen", "web", "vision"],
    ) == ["web"]


def test_hermes_safe_distribution_alias_keeps_image_gen_member() -> None:
    assert resolve_toolset_names(
        ["safe"],
        known_toolsets=["browser", "image_gen", "web", "vision"],
    ) == [
        "web",
        "browser",
        "vision",
        "image_gen",
    ]


def test_hermes_image_gen_distribution_alias_keeps_exact_members() -> None:
    assert resolve_toolset_names(
        ["image_gen"],
        known_toolsets=["image_gen", "terminal", "vision", "web"],
    ) == [
        "image_gen",
        "vision",
        "web",
        "terminal",
    ]


def test_hermes_gateway_platform_alias_recursively_expands_includes() -> None:
    known = [
        "browser",
        "computer",
        "discord",
        "discord_admin",
        "feishu_doc",
        "feishu_drive",
        "homeassistant",
        "voice",
        "web",
        "vision",
        "yuanbao",
    ]

    assert resolve_toolset_names(["hermes-gateway"], known_toolsets=known) == [
        "core",
        "web",
        "vision",
        "browser",
        "voice",
        "computer",
        "homeassistant",
        "discord",
        "discord_admin",
        "feishu_doc",
        "feishu_drive",
        "clarify",
        "yuanbao",
    ]


def test_available_accepts_recursive_hermes_platform_aliases() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_extract", toolset="web"))
    reg.register(SyntheticTool("browser_snapshot", toolset="browser"))
    reg.register(SyntheticTool("discord_admin", toolset="discord_admin"))
    reg.register(SyntheticTool("feishu_doc_read", toolset="feishu_doc"))
    reg.register(SyntheticTool("yb_query_group_info", toolset="yuanbao"))

    assert _names(reg.available(["hermes-gateway"], only_usable=False)) == [
        "core_read",
        "web_extract",
        "browser_snapshot",
        "discord_admin",
        "feishu_doc_read",
        "yb_query_group_info",
    ]


def test_disabled_hermes_platform_bundle_preserves_shared_core_toolsets() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_extract", toolset="web"))
    reg.register(SyntheticTool("discord_send", toolset="discord"))
    reg.register(SyntheticTool("discord_ban", toolset="discord_admin"))

    assert _names(
        reg.available(
            ["hermes-discord"],
            only_usable=False,
            disabled_toolsets=["hermes-discord"],
        )
    ) == ["core_read", "web_extract"]


def test_disabled_other_hermes_platform_bundle_does_not_strip_core() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_extract", toolset="web"))
    reg.register(SyntheticTool("yb_send_dm", toolset="yuanbao"))

    assert _names(
        reg.available(
            ["hermes-telegram"],
            only_usable=False,
            disabled_toolsets=["hermes-yuanbao"],
        )
    ) == ["core_read", "web_extract"]


def test_disabled_regular_toolset_still_subtracts_all_matching_tools() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_extract", toolset="web"))

    assert _names(
        reg.available(
            ["hermes-telegram"],
            only_usable=False,
            disabled_toolsets=["web"],
        )
    ) == ["core_read"]


def test_hermes_webhook_alias_does_not_enable_aegis_core() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_extract", toolset="web"))
    reg.register(SyntheticTool("vision_analyze", toolset="vision"))

    assert reg.resolve_toolsets(["hermes-webhook"]) == ["web", "vision", "clarify"]
    assert _names(reg.available(["hermes-webhook"], only_usable=False)) == [
        "web_extract",
        "vision_analyze",
    ]


def test_default_registry_hermes_leaf_toolsets_avoid_core_bleed() -> None:
    reg = default_registry(include_plugins=False)

    minimal = set(_names(reg.available(["minimal"], only_usable=False)))
    assert {"web_search", "web_extract"} <= minimal
    assert {"bash", "terminal", "read_file", "write_file", "generate_image"}.isdisjoint(
        minimal
    )

    safe = set(_names(reg.available(["safe"], only_usable=False)))
    assert {"web_search", "web_extract", "vision_analyze", "generate_image", "image_generate"} <= safe
    assert {"bash", "terminal", "read_file", "write_file"}.isdisjoint(safe)

    image_gen = set(_names(reg.available(["image_gen"], only_usable=False)))
    assert {"generate_image", "image_generate", "vision_analyze", "web_search", "bash"} <= image_gen
    assert {"read_file", "write_file", "patch"}.isdisjoint(image_gen)

    webhook = set(_names(reg.available(["hermes-webhook"], only_usable=False)))
    assert {"web_search", "web_extract", "vision_analyze", "clarify"} <= webhook
    assert {"bash", "terminal", "read_file", "write_file", "generate_image", "browser"}.isdisjoint(
        webhook
    )


class SequenceRng:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)

    def random(self) -> float:
        if not self.values:
            return 1.0
        return self.values.pop(0)


def test_hermes_distribution_records_are_listed_and_copied() -> None:
    from aegis.tools import registry as tool_registry

    assert validate_distribution("image_gen") is True
    assert validate_distribution("missing") is False

    dist = get_distribution("default")
    assert dist is not None
    assert dist["toolsets"] == {
        "web": 100,
        "vision": 100,
        "image_gen": 100,
        "terminal": 100,
        "file": 100,
        "browser": 100,
    }

    listed = list_distributions()
    assert listed is not tool_registry.HERMES_TOOLSET_DISTRIBUTIONS
    assert listed["default"] is dist
    assert {"default", "image_gen", "safe", "terminal_tasks"} <= set(listed)


def test_hermes_distribution_info_prints_sorted_toolsets(capsys) -> None:
    print_distribution_info("minimal")
    out = capsys.readouterr().out

    assert "Distribution: minimal" in out
    assert "web" in out
    assert "100% chance" in out

    print_distribution_info("missing")
    assert "Unknown distribution: missing" in capsys.readouterr().out


def test_hermes_distribution_sampling_selects_each_toolset_independently() -> None:
    sampled = sample_toolsets_from_distribution(
        "image_gen",
        rng=SequenceRng([0.00, 0.95, 0.54, 0.44]),
    )

    assert sampled == ["image_gen", "web", "terminal"]


def test_hermes_distribution_sampling_falls_back_to_highest_probability_toolset() -> None:
    assert sample_toolsets_from_distribution("research", rng=SequenceRng([1.0, 1.0, 1.0, 1.0])) == [
        "web"
    ]


def test_hermes_distribution_sampling_skips_fallback_when_highest_is_invalid(
    monkeypatch,
) -> None:
    from aegis.tools import registry as tool_registry

    monkeypatch.setitem(
        tool_registry.HERMES_TOOLSET_DISTRIBUTIONS,
            "fallback_valid",
            {
                "description": "Fallback only checks the highest-probability toolset.",
                "toolsets": {"web": 10, "browser": 75, "not_registered": 90},
            },
        )

    assert sample_toolsets_from_distribution(
        "fallback_valid",
        known_toolsets=["web", "browser"],
        rng=SequenceRng([1.0, 1.0, 1.0]),
    ) == []


def test_hermes_distribution_sampling_rejects_unknown_distribution() -> None:
    try:
        sample_toolsets_from_distribution("missing")
    except ValueError as exc:
        assert "Unknown distribution: missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing distribution should raise ValueError")


def test_unknown_hermes_platform_alias_expands_registered_platform_toolset() -> None:
    reg = ToolRegistry()
    reg.register(SyntheticTool("core_read", toolset="core"))
    reg.register(SyntheticTool("web_search", toolset="web"))
    reg.register(SyntheticTool("spotify_playback", toolset="spotify"))

    assert reg.resolve_toolsets(["hermes-spotify"]) == [
        "core",
        "web",
        "vision",
        "browser",
        "voice",
        "computer",
        "homeassistant",
        "spotify",
    ]
    assert _names(reg.available(["hermes-spotify"], only_usable=False)) == [
        "core_read",
        "web_search",
        "spotify_playback",
    ]
    assert _names(
        reg.available(
            ["hermes-spotify"],
            only_usable=False,
            disabled_toolsets=["hermes-spotify"],
        )
    ) == ["core_read", "web_search"]
