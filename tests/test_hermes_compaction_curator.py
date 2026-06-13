"""Hermes-parity batch: configurable compaction threshold, gateway hygiene safety-net,
curator run-gating + backups/rollback + reports, and the aligned default values."""

from __future__ import annotations

import types

from aegis import curator
from aegis.agent import compaction
from aegis.config import Config
from aegis.types import Message


# --------------------------------------------------------------------------- #
# C: configurable compaction threshold + aligned defaults
# --------------------------------------------------------------------------- #
def test_should_compress_honors_threshold():
    msgs = [Message.user("x" * 4000)]
    toks = compaction.estimated_tokens(msgs)
    ctx = int(toks / 0.7)                 # history fills ~70% of the window
    assert compaction.should_compress(msgs, ctx, 0, threshold=0.50) is True
    assert compaction.should_compress(msgs, ctx, 0, threshold=0.85) is False
    # default (no threshold passed) now uses the Hermes-aligned 0.50 constant
    assert compaction.should_compress(msgs, ctx, 0) is True


def test_should_compress_unknown_context_uses_default_window():
    msgs = [Message.user("short prompt")]
    assert compaction.should_compress(msgs, 0, 0) is False


def test_config_defaults_are_hermes_aligned():
    c = Config.load()
    assert c.get("agent.reasoning_effort") == "medium"
    assert c.get("agent.compression.threshold") == 0.50
    assert c.get("responses.compaction.compact_threshold") == 0.50
    assert c.get("agent.compression.gateway_hygiene_threshold") == 0.85
    assert c.get("agent.compression.hard_message_limit") == 400
    assert c.get("learn.memory_every") == 10
    assert c.get("learn.skill_every_iters") == 10
    assert c.get("curator.archive_after_days") == 90
    assert c.get("curator.interval_hours") == 168
    assert c.get("curator.min_idle_hours") == 2


# --------------------------------------------------------------------------- #
# A: gateway hygiene safety-net
# --------------------------------------------------------------------------- #
def _fake_agent(ctx: int = 1000):
    return types.SimpleNamespace(provider=types.SimpleNamespace(context_length=ctx))


def test_gateway_hygiene_fires_on_message_ceiling(monkeypatch):
    from aegis.gateway import runner

    called: dict = {}

    def fake_compact(agent, session=None, reason=""):
        called["reason"] = reason
        return session

    monkeypatch.setattr("aegis.agent.loop.compact_now", fake_compact)
    gw = runner.GatewayRunner(Config.load())
    sess = types.SimpleNamespace(messages=[Message.user("hi") for _ in range(401)])
    gw._gateway_hygiene(_fake_agent(), sess)
    assert called.get("reason") == "gateway_hygiene"


def test_gateway_hygiene_skips_small_session(monkeypatch):
    from aegis.gateway import runner

    called: dict = {}
    monkeypatch.setattr("aegis.agent.loop.compact_now",
                        lambda *a, **k: called.setdefault("hit", True))
    gw = runner.GatewayRunner(Config.load())
    # too few messages
    gw._gateway_hygiene(_fake_agent(), types.SimpleNamespace(messages=[Message.user("hi")]))
    # plenty of room left in a huge window
    gw._gateway_hygiene(_fake_agent(ctx=1_000_000),
                        types.SimpleNamespace(messages=[Message.user("hi") for _ in range(10)]))
    assert "hit" not in called


# --------------------------------------------------------------------------- #
# B: curator run-gating + backups/rollback + reports
# --------------------------------------------------------------------------- #
def test_curator_first_run_is_deferred():
    c = Config.load()
    assert curator.maybe_run(c) is None              # seeds the clock, defers
    assert curator._load_state().get("last_run_at")
    assert curator.maybe_run(c) is None              # interval not elapsed yet


def test_curator_runs_when_due_and_idle(monkeypatch):
    c = Config.load()
    curator._save_state({"last_run_at": "2000-01-01T00:00:00+00:00"})
    monkeypatch.setattr(curator, "_idle_hours", lambda now=None: 999.0)
    result = curator.maybe_run(c)
    assert result is not None and "report" in result
    from pathlib import Path
    assert (Path(result["report"]) / "REPORT.md").exists()
    assert curator._load_state()["last_run_at"] != "2000-01-01T00:00:00+00:00"


def test_curator_idle_gate_blocks_when_active(monkeypatch):
    c = Config.load()
    curator._save_state({"last_run_at": "2000-01-01T00:00:00+00:00"})
    monkeypatch.setattr(curator, "_idle_hours", lambda now=None: 0.1)
    assert curator.maybe_run(c) is None


def test_curator_disabled_never_runs():
    c = Config.load()
    c.data.setdefault("curator", {})["enabled"] = False
    assert curator.maybe_run(c) is None


def test_curator_backup_and_rollback_roundtrip():
    from aegis import config as cfg

    d = cfg.skills_dir() / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: demo\ndescription: x\n---\nbody\n")

    snap = curator.backup(reason="test")
    assert snap is not None and snap.exists()

    import shutil
    shutil.rmtree(d)
    assert not d.exists()

    restored = curator.rollback()
    assert restored
    assert (cfg.skills_dir() / "demo" / "SKILL.md").exists()


def test_curator_backup_prunes_to_keep():
    for i in range(8):
        curator.backup(reason=f"s{i}", keep=3)
    assert len(curator.list_backups()) <= 3
