"""Config hardening (raw YAML / defaults / backup / section reset) and live system stats."""

from __future__ import annotations

from aegis import dashboard as dash
from aegis.config import Config, config_path


def _cfg():
    return Config.load()


def test_config_raw_is_yaml_of_live_config():
    cfg = _cfg()
    cfg.set("model.provider", "anthropic")
    raw = dash._config_raw(cfg)
    assert raw["path"] == str(config_path())
    assert "model" in raw["raw"] and "anthropic" in raw["raw"]   # serialized YAML of config.data


def test_config_write_raw_validates_and_backs_up():
    cfg = _cfg()
    cfg.set("model.provider", "openai")          # ensure a file exists to back up
    res = dash._config_write_raw("model:\n  provider: anthropic\n  default: claude\n", cfg)
    assert res["ok"]
    assert res["backup"]                          # a .bak was written
    assert cfg.get("model.provider") == "anthropic"   # in-memory synced
    assert "anthropic" in config_path().read_text()   # persisted


def test_config_write_raw_rejects_bad_yaml():
    res = dash._config_write_raw("model: [unclosed", _cfg())
    assert res["ok"] is False and "YAML" in res["error"]


def test_config_write_raw_rejects_non_mapping():
    res = dash._config_write_raw("- just\n- a\n- list\n", _cfg())
    assert res["ok"] is False and "mapping" in res["error"]


def test_config_reset_section():
    cfg = _cfg()
    cfg.set("agent.max_iterations", 999)
    res = dash._config_reset_section("agent", cfg)
    assert res["ok"] and res["section"] == "agent"
    # value returns to the default (whatever DEFAULT_CONFIG says, not our 999)
    from aegis.config import DEFAULT_CONFIG
    assert cfg.get("agent.max_iterations") == DEFAULT_CONFIG.get("agent", {}).get("max_iterations")


def test_config_backup_now_creates_file():
    cfg = _cfg()
    cfg.set("model.provider", "anthropic")
    res = dash._config_backup_now()
    assert res["ok"] and res["backup"].endswith(".yaml.bak")
    from pathlib import Path
    assert Path(res["backup"]).exists()


def test_system_stats_shape():
    s = dash._system_stats()
    assert s["os"] and s["arch"] and s["python"]
    assert s["cpu_count"] >= 1
    assert s["disk_total_gb"] > 0
    # Linux CI: memory + uptime + load are present
    if s.get("mem_total_gb"):
        assert 0 <= s["mem_percent"] <= 100
