"""Per-tool on/off: the tools.disabled denylist hides individual tools from the model, and the
dashboard toggle endpoints edit tools.disabled / tools.toolsets."""

from __future__ import annotations

from aegis import dashboard as dash
from aegis.config import Config
from aegis.tools.registry import default_registry


def _a_core_tool() -> str:
    reg = default_registry()
    return reg.available(["core"], only_usable=False)[0].name


def test_disabled_denylist_hides_tool():
    reg = default_registry()
    name = _a_core_tool()
    visible = {t.name for t in reg.available(["core"], only_usable=False)}
    assert name in visible
    hidden = {t.name for t in reg.available(["core"], only_usable=False, disabled=[name])}
    assert name not in hidden
    assert hidden == visible - {name}


def test_tool_toggle_off_then_on():
    cfg = Config.load()
    name = _a_core_tool()

    off = dash._dashboard_tool_toggle({"name": name, "enabled": False}, cfg)
    assert off["ok"] and off["enabled"] is False
    assert name in (cfg.get("tools.disabled") or [])

    on = dash._dashboard_tool_toggle({"name": name, "enabled": True}, cfg)
    assert on["ok"] and on["enabled"] is True
    assert name not in (cfg.get("tools.disabled") or [])


def test_enable_tool_from_inactive_toolset_activates_it():
    cfg = Config.load()
    cfg.set("tools.toolsets", ["core"])
    # find a tool whose toolset isn't core
    reg = default_registry()
    other = next((t for t in reg.all() if t.toolset not in ("core", "all")), None)
    if other is None:
        return  # all tools are core in this build — nothing to assert
    res = dash._dashboard_tool_toggle({"name": other.name, "enabled": True}, cfg)
    assert res["ok"]
    assert other.toolset in (cfg.get("tools.toolsets") or [])


def test_toolset_toggle():
    cfg = Config.load()
    cfg.set("tools.toolsets", ["core"])
    on = dash._dashboard_toolset_toggle({"toolset": "web", "enabled": True}, cfg)
    assert "web" in on["toolsets"] and "web" in (cfg.get("tools.toolsets") or [])
    off = dash._dashboard_toolset_toggle({"toolset": "web", "enabled": False}, cfg)
    assert "web" not in off["toolsets"]


def test_toggle_unknown_tool_errors():
    res = dash._dashboard_tool_toggle({"name": "does_not_exist", "enabled": False}, Config.load())
    assert res["ok"] is False


def test_dashboard_tools_marks_off_state():
    cfg = Config.load()
    name = _a_core_tool()
    dash._dashboard_tool_toggle({"name": name, "enabled": False}, cfg)
    payload = dash._dashboard_tools(cfg)
    row = next(r for r in payload["tools"] if r["name"] == name)
    assert row["off"] is True and row["enabled"] is False
    assert name in payload["disabled"]


def test_dashboard_tool_schema_validation_payload():
    payload = dash._dashboard_tool_schema_validation(Config.load())
    assert payload["ok"] is True
    assert payload["valid"] == payload["total"]
    assert payload["issues"] == []


def test_dashboard_permission_dry_run_reports_policy_visibility_and_redacts_args():
    cfg = Config.load()
    cfg.data["tools"]["exec_mode"] = "ask"
    cfg.data["tools"]["toolsets"] = ["core"]
    cfg.data["tools"]["disabled"] = ["bash"]

    result = dash._dashboard_tool_permission_dry_run(
        {"tool": "bash", "args": {"command": "echo hi", "api_key": "sk-1234567890abcdef"}},
        cfg,
    )

    assert result["ok"] is True
    assert result["explanation"]["decision"] == "prompt"
    assert result["visibility"]["off"] is True
    assert result["visibility"]["enabled"] is False
    assert result["args"]["api_key"] == "[REDACTED]"
    assert result["authorize_without_approver"]["allowed"] is False
