"""Stage Z MCP startup discovery contracts."""

from __future__ import annotations

from types import SimpleNamespace


def _config_with_mcp_server():
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["mcp"] = {
        "enabled": True,
        "discovery_timeout": 0.5,
        "servers": {"local": {"command": "aegis-test-mcp-server"}},
    }
    return cfg


def test_background_mcp_discovery_skips_empty_config():
    from aegis.config import Config
    from aegis.mcp import startup

    startup.reset_background_mcp_discovery_for_tests()
    cfg = Config.load()
    cfg.data["mcp"] = {"enabled": True, "servers": {}}

    startup.start_background_mcp_discovery(cfg)

    assert startup.mcp_discovery_in_flight() is False
    assert startup.join_mcp_discovery(timeout=0) is True
    assert startup.claim_background_mcp_discovery(cfg, timeout=0) is None
    startup.reset_background_mcp_discovery_for_tests()


def test_background_mcp_discovery_is_one_shot_and_claimable(monkeypatch):
    from aegis.mcp import startup

    startup.reset_background_mcp_discovery_for_tests()
    cfg = _config_with_mcp_server()
    manager = SimpleNamespace(clients=["local"])
    calls = []

    def fake_discover(config):
        calls.append(config)
        return ["tool"], manager

    monkeypatch.setattr(startup, "_discover_mcp_tools", fake_discover)

    startup.start_background_mcp_discovery(cfg, thread_name="aegis-test-mcp")
    startup.start_background_mcp_discovery(cfg, thread_name="aegis-test-mcp-again")

    assert startup.join_mcp_discovery(timeout=2.0) is True
    assert calls == [cfg]
    assert startup.claim_background_mcp_discovery(cfg, timeout=0) == (["tool"], manager)
    assert startup.claim_background_mcp_discovery(cfg, timeout=0) is None
    startup.reset_background_mcp_discovery_for_tests()


def test_mcp_tools_from_config_uses_completed_background_discovery(monkeypatch):
    from aegis.mcp import startup
    from aegis.mcp.client import mcp_tools_from_config

    startup.reset_background_mcp_discovery_for_tests()
    cfg = _config_with_mcp_server()
    manager = SimpleNamespace(clients=["local"])

    def fake_discover(_config):
        return ["background-tool"], manager

    monkeypatch.setattr(startup, "_discover_mcp_tools", fake_discover)

    startup.start_background_mcp_discovery(cfg)
    assert startup.join_mcp_discovery(timeout=2.0) is True

    tools, claimed_manager = mcp_tools_from_config(cfg)

    assert tools == ["background-tool"]
    assert claimed_manager is manager
    assert startup.claim_background_mcp_discovery(cfg, timeout=0) is None
    startup.reset_background_mcp_discovery_for_tests()
