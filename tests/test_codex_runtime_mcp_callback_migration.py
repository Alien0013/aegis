"""Codex runtime MCP callback/config migration parity pins.

These tests intentionally describe the Hermes-first callback lane AEGIS should
grow for Codex app-server turns. They are tests-only pins: if the production
lane is missing, the failures should point at the missing surface.
"""

from __future__ import annotations

import importlib
import inspect
import io
import json
import sys
import tomllib
from pathlib import Path
from types import ModuleType


_SERVER_MODULE_CANDIDATES = (
    "aegis.mcp.aegis_tools_mcp_server",
    "aegis.agent.transports.aegis_tools_mcp_server",
    "aegis.transports.aegis_tools_mcp_server",
)

_MIGRATION_MODULE_CANDIDATES = (
    "aegis.cli.codex_runtime_plugin_migration",
    "aegis.codex_runtime_plugin_migration",
)

_CODEX_NATIVE_OR_LOOP_TOOLS = {
    "bash",
    "read_file",
    "write_file",
    "apply_patch",
    "todo_write",
    "memory",
    "session_search",
    "delegate_task",
}


def _import_first(candidates: tuple[str, ...]) -> ModuleType:
    errors: dict[str, str] = {}
    for name in candidates:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name and not name.startswith(f"{exc.name}."):
                raise
            errors[name] = str(exc)
    raise AssertionError(
        "Expected one of these AEGIS modules to exist: "
        + ", ".join(candidates)
        + f"; import errors: {errors}"
    )


def _call_migrate(module: ModuleType, config: dict, codex_home: Path):
    migrate = getattr(module, "migrate")
    kwargs = {
        "codex_home": codex_home,
        "dry_run": False,
        "discover_plugins": False,
        "default_permission_profile": ":workspace",
    }
    params = inspect.signature(migrate).parameters
    if "expose_aegis_tools" in params:
        kwargs["expose_aegis_tools"] = True
    elif "expose_callback_tools" in params:
        kwargs["expose_callback_tools"] = True
    elif "expose_hermes_tools" in params:
        kwargs["expose_hermes_tools"] = True
    return migrate(config, **{k: v for k, v in kwargs.items() if k in params})


def test_aegis_tools_callback_server_exposes_only_stateless_non_codex_tools() -> None:
    module = _import_first(_SERVER_MODULE_CANDIDATES)

    exposed = set(getattr(module, "EXPOSED_TOOLS"))

    assert {
        "web_search",
        "web_extract",
        "vision_analyze",
        "browser_navigate",
        "browser_snapshot",
        "skill_view",
        "skills_list",
    } <= exposed
    assert not (_CODEX_NATIVE_OR_LOOP_TOOLS & exposed)


def test_managed_codex_config_block_writes_aegis_tools_entry_and_preserves_user_toml(
    monkeypatch,
    tmp_path,
) -> None:
    module = _import_first(_MIGRATION_MODULE_CANDIDATES)
    monkeypatch.setattr(sys, "executable", "/venv/bin/python")
    monkeypatch.setenv("AEGIS_HOME", "/home/example/.aegis")
    monkeypatch.setenv("PYTHONPATH", "/worktree/aegis")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_path = codex_home / "config.toml"
    user_text = (
        'model = "gpt-5.5"\n'
        "\n"
        "[features]\n"
        "web_search = true\n"
    )
    config_path.write_text(user_text, encoding="utf-8")

    first = _call_migrate(module, {}, codex_home)
    first_text = config_path.read_text(encoding="utf-8")
    second = _call_migrate(module, {}, codex_home)
    second_text = config_path.read_text(encoding="utf-8")

    assert first_text == second_text
    assert getattr(first, "written") is True
    assert getattr(second, "written") is True
    assert first_text.count("[mcp_servers.aegis-tools]") == 1
    assert 'model = "gpt-5.5"' in first_text
    assert "[features]\nweb_search = true" in first_text

    parsed = tomllib.loads(first_text)
    entry = parsed["mcp_servers"]["aegis-tools"]
    assert entry["command"] == "/venv/bin/python"
    assert entry["args"][0] == "-m"
    assert "aegis" in entry["args"][1]
    assert "mcp" in entry["args"][1]
    assert "tool" in entry["args"][1]
    assert entry["env"]["AEGIS_HOME"] == "/home/example/.aegis"
    assert entry["env"]["PYTHONPATH"] == "/worktree/aegis"
    assert entry["env"]["AEGIS_QUIET"] == "1"
    assert entry["env"]["AEGIS_REDACT_SECRETS"] == "true"
    assert entry["startup_timeout_sec"] > 0
    assert entry["tool_timeout_sec"] > entry["startup_timeout_sec"]


def test_generic_aegis_mcp_server_still_exposes_configured_full_tool_inventory(
    monkeypatch,
    tmp_path,
) -> None:
    from aegis.config import Config
    from aegis.mcp.server import run_mcp_server
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry

    class NamedTool(Tool):
        description = "test tool"
        parameters = {"type": "object", "properties": {}}
        toolset = "core"

        def __init__(self, name: str) -> None:
            self.name = name

        def run(self, args, ctx):
            return ToolResult.ok(f"{self.name} ok")

    reg = ToolRegistry()
    for name in ["bash", "read_file", "write_file", "web_search", "vision_analyze"]:
        reg.register(NamedTool(name))

    class Perms:
        def __init__(self, config) -> None:
            self.config = config

        def authorize(self, tool, args, ctx):
            return True, ""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aegis.tools.registry.default_registry", lambda: reg)
    monkeypatch.setattr("aegis.tools.permissions.PermissionEngine", Perms)

    cfg = Config.load()
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("tools", {})["toolsets"] = ["core"]
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "bash", "arguments": {}},
        },
    ]
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"),
    )
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    run_mcp_server(cfg)

    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    listed = {tool["name"] for tool in rows[0]["result"]["tools"]}
    assert {"bash", "read_file", "write_file", "web_search", "vision_analyze"} <= listed
    assert rows[1]["result"]["content"][0]["text"] == "bash ok"


def test_codex_provider_metadata_carries_migration_payload_for_enum_api_mode() -> None:
    from types import SimpleNamespace

    from aegis.agent.loop import _provider_metadata
    from aegis.config import Config, DEFAULT_CONFIG
    from aegis.providers.base import ApiMode
    import copy

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data.setdefault("mcp", {})["servers"] = {
        "local": {"command": "python", "args": ["server.py"]}
    }
    agent = SimpleNamespace(
        config=cfg,
        provider=SimpleNamespace(api_mode=ApiMode.CODEX_APP_SERVER),
        session=SimpleNamespace(id="sess_1"),
        _trace_context={},
    )

    metadata = _provider_metadata(agent)

    payload = metadata["_codex_runtime_migration"]
    assert payload["migrate_config"] is True
    assert payload["expose_aegis_tools"] is True
    assert payload["mcp_servers"]["local"]["command"] == "python"
