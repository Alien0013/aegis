"""Tests for the Node/Ink terminal surface: built assets, launch gating, and the Python
WebSocket gateway protocol (handshake → ready → turn → frames)."""

from __future__ import annotations

import asyncio
import json
import sys
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
