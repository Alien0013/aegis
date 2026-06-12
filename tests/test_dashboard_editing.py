"""Dashboard is editable: env-token gating + the memory add/remove POST endpoint."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer


def _serve(cfg):
    from aegis.dashboard import make_handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _req(port, method, path, body=None, token="t"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"X-Aegis-Token": token, "Content-Type": "application/json"} if token else {}
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_env_token_gates_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "envtok")
    from aegis.config import Config
    from aegis.dashboard import _dashboard_token
    cfg = Config.load()
    assert _dashboard_token(cfg) == "envtok"          # env wins
    srv, port = _serve(cfg)
    try:
        assert _req(port, "GET", "/api/status", token=None)[0] == 401
        assert _req(port, "GET", "/api/status", token="envtok")[0] == 200
    finally:
        srv.shutdown()


def test_chat_event_row_carries_text_and_tool_id():
    """The Chat page needs streamed text, thinking, and paired tool ids to render
    the agent's words and live tool cards — _chat_event_row must preserve them."""
    from aegis.dashboard import _chat_event_row
    assert _chat_event_row({"type": "assistant_delta", "text": "hel"})["text"] == "hel"
    assert _chat_event_row({"type": "reasoning_delta", "text": "hmm"})["text"] == "hmm"
    start = _chat_event_row({"type": "tool_start", "id": "c1", "name": "bash",
                             "args": {"command": "ls -la"}})
    assert start["id"] == "c1" and start["target"] == "ls -la"
    res = _chat_event_row({"type": "tool_result", "id": "c1", "name": "bash", "preview": "ok"})
    assert res["id"] == "c1" and res["status"] == "ok" and res["target"] == "ok"
    err = _chat_event_row({"type": "tool_result", "id": "c2", "is_error": True})
    assert err["status"] == "error"
    it = _chat_event_row({"type": "iteration", "n": 2, "max": 30})
    assert it["n"] == 2 and it["max"] == 30


def test_memory_post_add_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_DASHBOARD_TOKEN", "t")
    from aegis.config import Config
    srv, port = _serve(Config.load())
    try:
        st, b = _req(port, "POST", "/api/memory", {"action": "add", "target": "user", "content": "Name: TJ"})
        assert st == 200 and "remembered" in b.get("result", "")
        st, b = _req(port, "GET", "/api/memory")
        assert "TJ" in b.get("user", "")
        st, b = _req(port, "POST", "/api/memory", {"action": "remove", "target": "user", "match": "TJ"})
        assert st == 200 and "removed" in b.get("result", "")
        # bad request
        assert _req(port, "POST", "/api/memory", {"action": "add", "target": "bogus", "content": "x"})[1].get("error")
    finally:
        srv.shutdown()
