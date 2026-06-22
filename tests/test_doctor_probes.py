"""Doctor deep probes + restart forensics."""

from __future__ import annotations

import json

from aegis import doctor
from aegis.config import Config


def test_channel_probes_fail_closed_without_tokens(monkeypatch):
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "WEBHOOK_CHANNEL_SECRET",
        "WEBHOOK_CHANNEL_ALLOW_UNSIGNED_LOOPBACK",
        "WEBHOOK_CHANNEL_INSECURE_NO_AUTH",
        "WHATSAPP_CHANNEL_SECRET",
        "WHATSAPP_CHANNEL_ALLOW_UNSIGNED_LOOPBACK",
        "WHATSAPP_CHANNEL_INSECURE_NO_AUTH",
    ):
        monkeypatch.delenv(var, raising=False)
    assert doctor.probe_telegram() == (False, "TELEGRAM_BOT_TOKEN not set")
    assert doctor.probe_discord()[0] is False
    assert doctor.probe_slack()[0] is False
    assert doctor.probe_webhook()[0] is False
    assert doctor.probe_whatsapp()[0] is False


def test_run_probes_counts_failures(monkeypatch):
    monkeypatch.setattr(doctor, "probe_provider", lambda c: (True, "stub ok"))
    monkeypatch.setattr(doctor, "CHANNEL_PROBES",
                        {"telegram": lambda: (False, "bad token")})
    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["channels"] = ["telegram", "signal"]
    lines = []
    failures = doctor.run_probes(cfg, out=lines.append)
    assert failures == 1
    joined = "\n".join(lines)
    assert "✓ provider" in joined and "✗ telegram" in joined and "– signal" in joined


def test_run_probes_detects_channel_env_without_gateway_config(monkeypatch):
    monkeypatch.setattr(doctor, "probe_provider", lambda c: (True, "stub ok"))
    for hints in doctor.CHANNEL_ENV_HINTS.values():
        for name in hints:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WHATSAPP_CHANNEL_SECRET", "bridge-secret")
    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["channels"] = []

    lines = []
    failures = doctor.run_probes(cfg, out=lines.append)

    assert failures == 0
    joined = "\n".join(lines)
    assert "✓ whatsapp" in joined
    assert "WHATSAPP_CHANNEL_SECRET configured" in joined


def test_release_preflight_reports_missing_and_ready_secret_names(monkeypatch):
    for name in (
        "DESKTOP_WINDOWS_CSC_LINK",
        "DESKTOP_WINDOWS_CSC_NAME",
        "DESKTOP_MAC_CSC_LINK",
        "DESKTOP_MAC_CSC_NAME",
        "CSC_LINK",
        "CSC_NAME",
        "APPLE_ID",
        "APPLE_APP_SPECIFIC_PASSWORD",
        "APPLE_ID_PASSWORD",
        "APPLE_TEAM_ID",
        "APPLE_ID_TEAM_ID",
        "APPLE_API_KEY",
        "APPLE_API_KEY_PATH",
        "APPLE_API_KEY_ID",
        "APPLE_API_KEYID",
        "APPLE_API_ISSUER",
        "APPLE_API_ISSUER_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    ok, rows = doctor.release_preflight(set())
    joined = "\n".join(rows)
    assert ok is False
    assert "missing DESKTOP_WINDOWS_CSC_LINK" in joined
    assert "missing DESKTOP_MAC_CSC_LINK" in joined
    assert "missing APPLE_ID" in joined

    ok, rows = doctor.release_preflight({
        "DESKTOP_WINDOWS_CSC_LINK",
        "DESKTOP_MAC_CSC_LINK",
        "APPLE_API_KEY",
        "APPLE_API_KEY_ID",
        "APPLE_API_ISSUER",
    })
    assert ok is True
    joined = "\n".join(rows)
    assert "Windows signing" in joined
    assert "App Store Connect API key notarization configured" in joined


def test_release_preflight_accepts_local_electron_alias_env(monkeypatch):
    monkeypatch.setenv("CSC_NAME", "Developer ID Application: AEGIS")
    monkeypatch.setenv("APPLE_ID", "dev@example.com")
    monkeypatch.setenv("APPLE_ID_PASSWORD", "app-password")
    monkeypatch.setenv("APPLE_ID_TEAM_ID", "TEAM123")

    ok, rows = doctor.release_preflight(set())

    assert ok is True
    assert "macOS signing" in "\n".join(rows)
    assert "Apple ID notarization configured" in "\n".join(rows)


def test_run_release_preflight_uses_visible_github_secret_names(monkeypatch):
    monkeypatch.setattr(doctor, "github_secret_names", lambda: ({
        "DESKTOP_WINDOWS_CSC_NAME",
        "DESKTOP_MAC_CSC_NAME",
        "APPLE_ID",
        "APPLE_APP_SPECIFIC_PASSWORD",
        "APPLE_TEAM_ID",
    }, "Alien0013/aegis: 5 secret name(s) visible"))
    lines = []
    assert doctor.run_release_preflight(out=lines.append) == 0
    joined = "\n".join(lines)
    assert "desktop release signing preflight" in joined
    assert "signed Windows and signed/notarized macOS" in joined


def test_crash_report_detects_unclean_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    assert doctor.crash_report() == ""             # no history -> no report
    doctor.record_start()
    rep = doctor.crash_report()                    # START with no shutdown after it
    assert "without a clean shutdown" in rep
    # a clean shutdown record clears the condition
    with open(tmp_path / "logs" / "shutdowns.jsonl", "a") as f:
        f.write(json.dumps({"at": "now", "cause": "SIGTERM", "pid": 1}) + "\n")
    assert doctor.crash_report() == ""
