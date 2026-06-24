from __future__ import annotations

import json
import argparse

import pytest
import yaml


def test_hermes_compat_top_level_commands_are_registered():
    from aegis.cli.main import build_parser

    parser = build_parser()
    choices = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices.update(action.choices)

    expected = {
        "bundles",
        "claw",
        "computer-use",
        "dump",
        "fallback",
        "gui",
        "login",
        "logout",
        "lsp",
        "migrate",
        "pets",
        "portal",
        "postinstall",
        "prompt-size",
        "proxy",
        "send",
        "slack",
        "version",
        "whatsapp",
        "whatsapp-cloud",
    }
    assert expected <= choices


def test_config_summary_is_readable_ascii_and_redacts_secret(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")
    monkeypatch.delenv("AEGIS_UNICODE", raising=False)
    monkeypatch.delenv("AEGIS_ASCII", raising=False)

    from aegis import config as cfg
    from aegis.cli.main import main

    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text(
        "model:\n"
        "  provider: openai\n"
        "  default: gpt-5\n"
        "gateway:\n"
        "  channels: [telegram]\n",
        encoding="utf-8",
    )

    assert main(["config"]) == 0

    out = capsys.readouterr().out
    out.encode("ascii")
    assert "AEGIS Configuration" in out
    assert "== Paths ==" in out
    assert "Workspace:" in out
    assert "== API Keys ==" in out
    assert "OpenAI" in out
    assert "Qwen" in out
    assert "MiniMax" in out
    assert "Cerebras" in out
    assert "(set," in out
    assert "sk-test-secret-value" not in out
    assert "telegram-secret" not in out
    assert "Telegram:   configured" in out
    assert "Input mode:" in out
    assert "aegis config setup memory" in out
    assert "aegis chat" in out
    assert "aegis setup dashboard" in out

    assert main(["config", "view"]) == 0
    out = capsys.readouterr().out
    assert "AEGIS Configuration" in out


def test_config_summary_can_use_unicode_terminal_skin(monkeypatch, capsys):
    monkeypatch.setenv("AEGIS_UNICODE", "1")
    monkeypatch.delenv("AEGIS_ASCII", raising=False)

    from aegis.cli.main import main

    assert main(["config"]) == 0

    out = capsys.readouterr().out
    assert "╭" in out
    assert "◇ Paths" in out
    assert "◇ Commands" in out
    assert "== Paths ==" not in out


def test_config_status_json_is_machine_readable_and_redacted(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")

    from aegis import config as cfg
    from aegis.cli.main import main

    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text(
        "model:\n"
        "  provider: openai\n"
        "  default: gpt-5.5\n"
        "display:\n"
        "  timestamps: true\n"
        "server:\n"
        "  api_key: server-secret\n"
        "gateway:\n"
        "  channels: [telegram]\n",
        encoding="utf-8",
    )

    assert main(["config", "status", "--json"]) == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["object"] == "aegis.config.status"
    assert data["paths"]["config"] == str(cfg.config_path())
    assert data["paths"]["secrets"] == str(cfg.env_path())
    assert data["paths"]["workspace"] == str(cfg.workspace_dir())
    assert data["services"]["api_auth_configured"] is True
    assert data["api_keys"]["OpenAI"]["set"] is True
    assert data["api_keys"]["OpenAI"]["name"] == "OPENAI_API_KEY"
    assert data["api_keys"]["OpenAI"]["source"] == "OPENAI_API_KEY"
    assert data["api_keys"]["OpenAI"]["preview"] != "sk-test-secret-value"
    assert data["api_keys"]["OpenAI"]["chars"] == len("sk-test-secret-value")
    assert data["api_keys"]["Qwen"]["env"] == ["QWEN_API_KEY", "DASHSCOPE_API_KEY"]
    assert data["api_keys"]["MiniMax"]["env"] == ["MINIMAX_API_KEY"]
    assert data["api_keys"]["Cerebras"]["env"] == ["CEREBRAS_API_KEY"]
    assert data["model"]["provider"] == "openai"
    assert data["model"]["default"] == "gpt-5.5"
    assert data["display"]["timestamps"] is True
    assert data["terminal"]["exec_mode"] == "auto"
    assert data["terminal"]["busy_mode"] == "queue"
    assert "active run" in data["terminal"]["busy_mode_hint"]
    assert data["terminal"]["subagent_backend"] == "(inherit)"
    assert data["terminal"]["allow_local_fallback"] is False
    assert data["messaging_platforms"]["telegram"] == "configured"
    assert "matrix" in data["messaging_platforms"]
    assert "signal" in data["messaging_platforms"]
    assert data["messaging_platform_details"]["telegram"]["display_name"] == "Telegram"
    assert data["validation"]["config_yaml"] == "ok"
    assert "aegis config view" in data["commands"]
    assert "aegis config edit" in data["commands"]
    assert "aegis chat" in data["commands"]
    assert "aegis config setup memory" in data["commands"]
    assert "aegis setup dashboard" in data["commands"]
    assert "sk-test-secret-value" not in out
    assert "telegram-secret" not in out
    assert "server-secret" not in out


def test_config_edit_without_editor_prints_path(monkeypatch, capsys):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("PATH", "")

    from aegis import config as cfg
    from aegis.cli.main import main

    assert main(["config", "edit"]) == 1

    out = capsys.readouterr().out
    assert "No editor found" in out
    assert str(cfg.config_path()) in out
    assert cfg.config_path().exists()


def test_config_set_dotted_yaml_preserves_unrelated_config(capsys):
    from aegis import config as cfg
    from aegis.cli.main import main

    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text(
        "custom_providers:\n"
        "- name: provider-a\n"
        "  env_var: OLD_A_KEY\n"
        "  base_url: https://a.example.test/v1\n"
        "- name: provider-b\n"
        "  env_var: OLD_B_KEY\n"
        "  base_url: https://b.example.test/v1\n"
        "display:\n"
        "  platforms:\n"
        "    telegram:\n"
        "      memory_notifications: verbose\n",
        encoding="utf-8",
    )

    assert main(["config", "set", "custom_providers.0.env_var", "NEW_A_KEY"]) == 0

    out = capsys.readouterr().out
    assert "set custom_providers.0.env_var -> config.yaml" in out
    data = yaml.safe_load(cfg.config_path().read_text(encoding="utf-8"))
    assert data["custom_providers"] == [
        {
            "name": "provider-a",
            "env_var": "NEW_A_KEY",
            "base_url": "https://a.example.test/v1",
        },
        {
            "name": "provider-b",
            "env_var": "OLD_B_KEY",
            "base_url": "https://b.example.test/v1",
        },
    ]
    assert data["display"]["platforms"]["telegram"]["memory_notifications"] == "verbose"


def test_config_set_unknown_key_requires_force(capsys):
    from aegis.config import Config
    from aegis.cli.main import main

    assert main(["config", "set", "model.defualt", "gpt-5"]) == 1
    captured = capsys.readouterr()
    assert "unknown config key: model.defualt" in captured.err
    assert "--force" in captured.err
    assert Config.load().get("model.defualt") is None

    assert main(["config", "set", "model.defualt", "gpt-5", "--force"]) == 0
    out = capsys.readouterr().out
    assert "set model.defualt -> config.yaml" in out
    assert Config.load().get("model.defualt") == "gpt-5"

    assert main(["config", "set", "model.unknown", "x", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["key"] == "model.unknown"
    assert "unknown config key" in data["error"]
    assert "--force" in data["error"]


def test_config_set_preserves_comments_and_readable_unicode(capsys):
    from aegis import config as cfg
    from aegis.cli.main import main

    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text(
        "# keep this model note\n"
        "model:\n"
        "  provider: openai\n"
        "  default: gpt-5\n",
        encoding="utf-8",
    )

    assert main(["config", "set", "agent.personality", "café"]) == 0

    out = capsys.readouterr().out
    saved = cfg.config_path().read_text(encoding="utf-8")
    assert "set agent.personality -> config.yaml" in out
    assert "# keep this model note" in saved
    assert "café" in saved
    assert "\\u" not in saved


def test_config_reset_key_section_and_custom_override(capsys):
    from aegis import config as cfg
    from aegis.cli.main import main
    from aegis.config import Config, DEFAULT_CONFIG

    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text(
        "# keep reset note\n"
        "model:\n"
        "  provider: openai\n"
        "  default: gpt-5.5\n"
        "agent:\n"
        "  max_iterations: 9\n"
        "custom_block:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )

    assert main(["config", "reset", "model.default"]) == 0
    out = capsys.readouterr().out
    assert "reset model.default -> default" in out
    assert "backup:" in out
    data = yaml.safe_load(cfg.config_path().read_text(encoding="utf-8")) or {}
    assert data["model"] == {"provider": "openai"}
    assert Config.load().get("model.default") == DEFAULT_CONFIG["model"]["default"]
    assert "# keep reset note" in cfg.config_path().read_text(encoding="utf-8")
    assert list(cfg.config_path().parent.glob("config.yaml.bak-*"))

    assert main(["config", "reset", "agent"]) == 0
    capsys.readouterr()
    data = yaml.safe_load(cfg.config_path().read_text(encoding="utf-8")) or {}
    assert "agent" not in data
    assert Config.load().get("agent.max_iterations") == DEFAULT_CONFIG["agent"]["max_iterations"]

    assert main(["config", "reset", "custom_block"]) == 0
    capsys.readouterr()
    data = yaml.safe_load(cfg.config_path().read_text(encoding="utf-8")) or {}
    assert "custom_block" not in data


def test_config_reset_unknown_key_errors(capsys):
    from aegis.cli.main import main

    assert main(["config", "reset", "does.not.exist"]) == 1
    err = capsys.readouterr().err
    assert "unknown config key: does.not.exist" in err


def test_config_mutation_commands_support_json(capsys):
    from aegis import config as cfg
    from aegis.cli.main import main
    from aegis.config import Config, DEFAULT_CONFIG

    cfg.config_path().parent.mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text("model:\n  default: gpt-5.5\n", encoding="utf-8")

    assert main(["config", "paths", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["object"] == "aegis.config.paths"
    assert data["paths"]["config"] == str(cfg.config_path())

    assert main(["config", "get", "model.default", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "ok": True,
        "object": "aegis.config.value",
        "key": "model.default",
        "source": "config",
        "value": "gpt-5.5",
    }

    assert main(["config", "set", "tools.exec_mode", "smart", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["action"] == "set"
    assert data["key"] == "tools.exec_mode"
    assert data["value"] == "smart"
    assert data["where"].startswith("config.yaml")
    assert Config.load().get("tools.exec_mode") == "smart"

    assert main(["config", "set", "tools.exec_mode", "bogus", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["key"] == "tools.exec_mode"
    assert "one of" in data["error"]
    assert Config.load().get("tools.exec_mode") == "smart"

    assert main(["config", "reset", "tools.exec_mode", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["action"] == "reset"
    assert data["key"] == "tools.exec_mode"
    assert data["backup"]
    assert Config.load().get("tools.exec_mode") == DEFAULT_CONFIG["tools"]["exec_mode"]

    assert main(["config", "check", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["object"] == "aegis.config.check"
    assert data["ok"] is True
    assert data["type_errors"] == []

    before = cfg.config_path().read_text(encoding="utf-8")
    assert main(["config", "migrate", "--dry-run", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["object"] == "aegis.config.migration_preview"
    assert data["dry_run"] is True
    assert data["ok"] is True
    assert "normalized_delta" in data
    assert cfg.config_path().read_text(encoding="utf-8") == before


def test_config_json_redacts_secret_values(monkeypatch, capsys):
    from aegis.cli.main import main

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["config", "set", "OPENAI_API_KEY", "sk-env-secret", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["value"] == "[REDACTED]"
    assert "sk-env-secret" not in json.dumps(data)

    assert main(["config", "get", "OPENAI_API_KEY", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["source"] == ".env"
    assert data["value"] == "[REDACTED]"


def test_config_env_writer_rejects_denylisted_names(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis import config as cfg
    from aegis.cli.main import main

    with pytest.raises(ValueError, match="writer denylist"):
        cfg.set_env_var("PATH", "/tmp/bin")

    assert main(["config", "set", "PATH", "/tmp/bin", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert "writer denylist" in data["error"]
    assert not cfg.env_path().exists()
