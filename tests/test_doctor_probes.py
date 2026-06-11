"""Doctor deep probes + restart forensics."""

from __future__ import annotations

import json

from aegis import doctor
from aegis.config import Config


def test_channel_probes_fail_closed_without_tokens(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert doctor.probe_telegram() == (False, "TELEGRAM_BOT_TOKEN not set")
    assert doctor.probe_discord()[0] is False
    assert doctor.probe_slack()[0] is False


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
