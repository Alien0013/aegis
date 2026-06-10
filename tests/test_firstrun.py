"""First-touch hints: one-shot flags, busy hints, profile-build directive."""

from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    return Config.load()


def test_mark_seen_persists(tmp_path, monkeypatch):
    from aegis import firstrun
    config = _cfg(tmp_path, monkeypatch)
    assert not firstrun.is_seen(config, "x")
    firstrun.mark_seen(config, "x")
    assert firstrun.is_seen(config, "x")
    # persisted to disk, not just in-memory
    from aegis.config import Config
    assert firstrun.is_seen(Config.load(), "x")


def test_busy_hint_matches_mode():
    from aegis.firstrun import busy_hint
    assert "queued" in busy_hint("queue")
    assert "folded" in busy_hint("steer")
    assert "interrupted" in busy_hint("interrupt")


def test_profile_directive_is_one_shot_and_can_be_disabled(tmp_path, monkeypatch):
    from aegis.firstrun import profile_build_directive
    config = _cfg(tmp_path, monkeypatch)
    first = profile_build_directive(config)
    assert "OFFER" in first and "consent" in first
    assert profile_build_directive(config) == ""        # only the very first message

    config2 = _cfg(tmp_path, monkeypatch)
    config2.set("onboarding.profile_build", "off")
    config2.data["onboarding"]["seen"] = {}
    assert profile_build_directive(config2) == ""       # disabled entirely


def test_telegram_busy_mode_steer_and_hint(tmp_path, monkeypatch):
    """A message landing while a turn runs: steer mode folds it in; first time adds the tip."""
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:FAKEfakeFAKEfakeFAKEfakeFAKEfake000")
    from aegis.config import Config
    from aegis.gateway.base import MessageEvent
    from aegis.gateway.channels import TelegramAdapter

    adapter = TelegramAdapter()
    config = Config.load()
    config.data["gateway"]["busy_mode"] = "steer"
    adapter._config = config
    steered = []
    adapter._steer_cb = lambda ev, text: steered.append(text) or True

    ev = MessageEvent(platform="telegram", chat_id="c1", text="also check the tests",
                      user_id="u1")
    handled, note = adapter._apply_busy_mode(ev)
    assert handled and steered == ["also check the tests"]
    assert "First-time tip" in note
    # second time: no hint
    handled, note = adapter._apply_busy_mode(ev)
    assert handled and note == ""
