"""Tests for the Node/Ink terminal surface: built assets, launch gating, and the Python
WebSocket gateway protocol (handshake → ready → turn → frames)."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import types
from pathlib import Path

import pytest

from aegis.cli import repl, tui_ink
from aegis.config import Config

_AEGIS = Path(__file__).resolve().parent.parent / "aegis"
_INK = _AEGIS / "tui_ink"


# --------------------------------------------------------------------------- assets
def test_ink_bundle_and_license_present():
    assert (_INK / "dist" / "entry.js").is_file(), "Ink bundle must be prebuilt and shipped"
    assert (_INK / "dist" / "entry.js.LEGAL.txt").is_file(), "bundled-dep license notices must ship"
    assert (_INK / "package.json").is_file()
    assert (_INK / "src" / "entry.tsx").is_file()


def test_package_json_declares_ink_deps():
    pkg = json.loads((_INK / "package.json").read_text())
    deps = pkg.get("dependencies", {})
    for name in ("ink", "react", "ws", "ink-text-input"):
        assert name in deps, f"{name} should be a declared dependency"


def test_tui_setup_parity_sources_are_aegis_native():
    required = [
        "src/bootstrap/state.ts",
        "src/ink/log-update.ts",
        "src/lib/terminalSetup.ts",
        "src/app/setupHandoff.ts",
        "src/app/slash/commands/setup.ts",
        "src/content/setup.ts",
    ]
    for rel in required:
        path = _INK / rel
        assert path.is_file(), rel
        text = path.read_text(encoding="utf-8")
        assert "AEGIS" in text or "aegis" in text


def test_repl_exposes_setup_slash_command():
    commands = {command.name: command for command in repl.SLASH_COMMANDS}
    assert "/setup" in commands
    assert "aegis setup" in commands["/setup"].summary


def test_gateway_setup_status_uses_provider_readiness(monkeypatch):
    from aegis import tui_gateway

    monkeypatch.setattr(
        "aegis.providers.registry.provider_capability_matrix",
        lambda _config: {"totals": {"ready": 0}, "active": {"provider": "anthropic", "model": "claude"}},
    )
    missing = tui_gateway.setup_status(Config.load())
    assert missing["provider_configured"] is False
    assert missing["provider"] == "anthropic"

    monkeypatch.setattr(
        "aegis.providers.registry.provider_capability_matrix",
        lambda _config: {"totals": {"ready": 1}, "active": {"provider": "openai", "model": "gpt-5.5"}},
    )
    ready = tui_gateway.setup_status(Config.load())
    assert ready == {"provider_configured": True, "provider": "openai", "model": "gpt-5.5"}


def test_gateway_ready_header_merges_setup_readiness(monkeypatch):
    from aegis import tui_gateway

    monkeypatch.setattr(tui_gateway, "header_snapshot", lambda _agent: {"brand": "AEGIS"})
    monkeypatch.setattr(
        tui_gateway,
        "setup_status",
        lambda _config: {"provider_configured": False, "provider": "openai", "model": "gpt-test"},
    )

    header = tui_gateway.ready_header(object(), Config.load())

    assert header["brand"] == "AEGIS"
    assert header["setup"] == {"provider_configured": False, "provider": "openai", "model": "gpt-test"}


async def _wait_until(predicate, *, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_gateway_busy_input_queues_and_drains_after_current_turn(monkeypatch):
    from aegis.tui_gateway import TuiGateway

    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["busy_mode"] = "queue"
    gateway = TuiGateway(cfg)
    gateway._agent = types.SimpleNamespace(config=cfg)
    frames: list[dict] = []
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    async def fake_status(*, running: bool):
        frames.append({"type": "status", "running": running})

    def fake_turn(text: str):
        calls.append(text)
        if text == "first":
            started.set()
            assert release.wait(2), "test did not release the first turn"

    monkeypatch.setattr(gateway, "_emit_threadsafe", frames.append)
    monkeypatch.setattr(gateway, "_emit_status", fake_status)
    monkeypatch.setattr(gateway, "_do_turn", fake_turn)

    async def drive():
        await gateway._dispatch(None, {"type": "input", "text": "first"})
        assert await asyncio.to_thread(started.wait, 2)
        await gateway._dispatch(None, {"type": "input", "text": "second"})
        release.set()
        await _wait_until(lambda: calls == ["first", "second"] and gateway._running is False)

    asyncio.run(drive())

    assert calls == ["first", "second"]
    assert any(
        frame.get("type") == "output" and "queued input" in frame.get("text", "")
        for frame in frames
    )


def test_gateway_busy_interrupt_cancels_and_queues(monkeypatch):
    from aegis.tui_gateway import TuiGateway

    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["busy_mode"] = "interrupt"
    cancel_calls: list[bool] = []
    gateway = TuiGateway(cfg)
    gateway._agent = types.SimpleNamespace(config=cfg, cancel=lambda: cancel_calls.append(True))
    frames: list[dict] = []
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    async def fake_status(*, running: bool):
        frames.append({"type": "status", "running": running})

    def fake_turn(text: str):
        calls.append(text)
        if text == "first":
            started.set()
            assert release.wait(2), "test did not release the first turn"

    monkeypatch.setattr(gateway, "_emit_threadsafe", frames.append)
    monkeypatch.setattr(gateway, "_emit_status", fake_status)
    monkeypatch.setattr(gateway, "_do_turn", fake_turn)

    async def drive():
        await gateway._dispatch(None, {"type": "input", "text": "first"})
        assert await asyncio.to_thread(started.wait, 2)
        await gateway._dispatch(None, {"type": "input", "text": "second"})
        assert cancel_calls == [True]
        release.set()
        await _wait_until(lambda: calls == ["first", "second"] and gateway._running is False)

    asyncio.run(drive())

    assert calls == ["first", "second"]
    assert any(
        frame.get("type") == "output" and "interrupting current turn" in frame.get("text", "")
        for frame in frames
    )


def test_gateway_busy_steer_uses_live_agent_guidance(monkeypatch):
    from aegis.tui_gateway import TuiGateway

    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["busy_mode"] = "steer"
    steered: list[str] = []
    gateway = TuiGateway(cfg)
    gateway._running = True
    gateway._agent = types.SimpleNamespace(
        config=cfg,
        steer=lambda text: steered.append(text) or True,
    )
    frames: list[dict] = []
    monkeypatch.setattr(gateway, "_emit_threadsafe", frames.append)

    asyncio.run(gateway._dispatch(None, {"type": "input", "text": "nudge the run"}))

    assert steered == ["nudge the run"]
    assert gateway._running is True
    assert any(
        frame.get("type") == "output" and "steered input" in frame.get("text", "")
        for frame in frames
    )


# ---------------------------------------------------------------------------- gating
def test_launch_requires_node(monkeypatch):
    monkeypatch.setattr(tui_ink.shutil, "which", lambda _name: None)
    with pytest.raises(repl._FullscreenUnavailable):
        tui_ink.launch_ink_tui(Config.load())


def test_launch_requires_built_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(tui_ink.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(tui_ink, "_ink_entry", lambda: tmp_path / "missing.js")
    with pytest.raises(repl._FullscreenUnavailable):
        tui_ink.launch_ink_tui(Config.load())


def test_fullscreen_enabled_respects_classic_env(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    cfg = Config.load()
    monkeypatch.setenv("AEGIS_CLASSIC_TUI", "1")
    assert repl._fullscreen_enabled(cfg) is False
    monkeypatch.delenv("AEGIS_CLASSIC_TUI", raising=False)
    # With a TTY, no opt-out, and no running loop, the full-screen surface is selected.
    assert repl._fullscreen_enabled(cfg) is True


def test_fullscreen_disabled_without_tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    assert repl._fullscreen_enabled(Config.load()) is False


# --------------------------------------------------------------------- refactor seam
def test_repl_exposes_shared_terminal_helpers():
    assert callable(repl.build_terminal_agent)
    assert callable(repl.process_terminal_input)


# ------------------------------------------------------------------- gateway protocol
def _build_agent():
    return repl.build_terminal_agent(Config.load())


def test_header_snapshot_shape():
    from aegis.tui_gateway import header_snapshot

    _runner, agent = _build_agent()
    snap = header_snapshot(agent)
    for key in ("brand", "model", "session_id", "ctx_window", "reasoning", "perms", "busy"):
        assert key in snap, f"header snapshot missing {key}"
    assert snap["brand"] == "AEGIS"


def test_structured_emitter_forwards_known_events():
    from aegis.tui_gateway import _StructuredEmitter

    frames = []
    emit = _StructuredEmitter(frames.append)
    emit({"type": "assistant_delta", "text": "hello"})
    emit({"type": "tool_start", "name": "bash", "preview": "ls"})
    emit({"type": "tool_result", "name": "bash", "summary": "ok", "duration_ms": 12, "is_error": False})
    emit({"type": "provider_start", "provider": "x"})  # not forwarded (status noise)
    kinds = [f["event"]["type"] for f in frames]
    assert kinds == ["assistant_delta", "tool_start", "tool_result"]
    assert all(f["type"] == "event" for f in frames)
    # forwarded frames keep only safe, JSON-serialisable fields
    json.dumps(frames)


def test_safe_event_stringifies_unknown_values():
    from aegis.tui_gateway import _safe_event

    class Weird:
        def __str__(self):
            return "weird-repr"

    safe = _safe_event({"type": "tool_result", "name": Weird(), "duration_ms": 5, "extra": object()})
    assert safe["name"] == "weird-repr"
    assert safe["duration_ms"] == 5
    assert "extra" not in safe  # only whitelisted keys survive
    json.dumps(safe)


def test_ink_composer_keeps_busy_submissions_for_gateway_queue():
    src = (_INK / "src" / "entry.tsx").read_text(encoding="utf-8")
    assert "if (running) { setPending([]); return; }" not in src
    assert "busy submission is sent to the gateway" in src


def test_ink_banner_surfaces_setup_readiness():
    src = (_INK / "src" / "entry.tsx").read_text(encoding="utf-8")
    assert "provider setup needed" in src
    assert "run /setup" in src


def test_gateway_handshake_and_turn():
    """End-to-end: a websocket client completes the handshake and runs a no-LLM slash
    command through the gateway, receiving streamed output and a turn_done frame."""
    import websockets

    from aegis.tui_gateway import start_gateway_thread

    host, port, token, stop = start_gateway_thread(Config.load())
    assert port, "gateway failed to bind"

    async def drive():
        async with websockets.connect(f"ws://{host}:{port}") as ws:
            # bad token is rejected
            await ws.send(json.dumps({"type": "hello", "token": "wrong"}))
            # server closes on bad token; reconnect for the real run
        async with websockets.connect(f"ws://{host}:{port}") as ws:
            await ws.send(json.dumps({"type": "hello", "token": token}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            assert ready["type"] == "ready"
            assert ready["header"]["brand"] == "AEGIS"
            await ws.send(json.dumps({"type": "input", "text": "/version"}))
            outputs, done = [], False
            for _ in range(500):
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if frame["type"] == "output":
                    outputs.append(frame["text"])
                elif frame["type"] == "turn_done":
                    done = True
                    break
            assert done, "no turn_done frame received"
            assert "".join(outputs).strip(), "no output streamed for /version"

    try:
        asyncio.run(drive())
    finally:
        stop()
