from __future__ import annotations

import json

import yaml


def test_config_summary_is_readable_ascii_and_redacts_secret(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")

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
    assert "== API Keys ==" in out
    assert "OpenAI" in out
    assert "(set," in out
    assert "sk-test-secret-value" not in out
    assert "telegram-secret" not in out
    assert "Telegram:   configured" in out


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
    assert data["services"]["api_auth_configured"] is True
    assert data["api_keys"]["OpenAI"]["set"] is True
    assert data["api_keys"]["OpenAI"]["name"] == "OPENAI_API_KEY"
    assert data["api_keys"]["OpenAI"]["chars"] == len("sk-test-secret-value")
    assert data["model"]["provider"] == "openai"
    assert data["model"]["default"] == "gpt-5.5"
    assert data["messaging_platforms"]["telegram"] == "configured"
    assert data["validation"]["config_yaml"] == "ok"
    assert "aegis config edit" in data["commands"]
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
