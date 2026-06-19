from __future__ import annotations

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
