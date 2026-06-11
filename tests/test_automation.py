"""Cron + webhook automation: skill chaining, script context, [SILENT], multi-deliver."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer


# --- shared helpers ---------------------------------------------------------
def test_automation_helpers(tmp_path):
    from aegis import automation as a
    assert a.skills_directive(["x", "y"]).startswith("Load these skills first: x, y")
    assert a.skills_directive([]) == ""
    assert a.is_silent("") and a.is_silent("  [SILENT] nothing changed") and a.is_silent("[silent]")
    assert not a.is_silent("real reply")
    assert a.delivery_targets("telegram:1, discord:2 ,") == ["telegram:1", "discord:2"]

    script = tmp_path / "ctx.py"
    script.write_text("print('HELLO CONTEXT')")
    out = a.script_context(str(script))
    assert "# Context" in out and "HELLO CONTEXT" in out
    assert a.script_context("/no/such/file.py") == ""              # fail-soft
    prompt = a.build_prompt("do the thing", skills=["s1"], script=str(script))
    assert "Load these skills first: s1" in prompt and "HELLO CONTEXT" in prompt and prompt.endswith("do the thing")


def test_cron_delivery_sink_sends_via_configured_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.cron import build_delivery_sink
    import aegis.gateway.channels as channels

    sent = []

    class FakeAdapter:
        name = "telegram"
        def send(self, chat_id, text):
            sent.append((chat_id, text))

    cfg = Config.load()
    cfg.data.setdefault("gateway", {})["channels"] = ["telegram"]
    monkeypatch.setattr(channels, "build_adapter", lambda _name, _config: FakeAdapter())

    build_delivery_sink(cfg, verbose=False)("telegram:42", "daily brief")

    assert sent == [("42", "daily brief")]


# --- TASK 1: webhooks deliver / filter / chain skills -----------------------
def _fake_agent(monkeypatch, reply="hello"):
    import aegis.agent.agent as am
    seen = {}

    class A:
        def run(self, prompt):
            seen["prompt"] = prompt
            return type("R", (), {"content": reply})()
    monkeypatch.setattr(am.Agent, "create", staticmethod(lambda cfg, session=None: A()))
    return seen


def _webhook_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    from aegis.webhook import WebhookStore, make_handler
    return Config.load(), WebhookStore(), make_handler


def _post(port, path, body=b"{}", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, body, headers or {})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def _serve(make_handler, config, store):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_webhook_event_filter_skips(monkeypatch, tmp_path):
    cfg, store, make_handler = _webhook_server(monkeypatch, tmp_path)
    _fake_agent(monkeypatch)
    store.add("ci", "review {action}", events=["pull_request"])
    srv, port = _serve(make_handler, cfg, store)
    try:
        status, body = _post(port, "/hook/ci", b'{"action":"opened"}', {"X-GitHub-Event": "push"})
        assert status == 200 and body.get("skipped") == "event"     # push not in allowlist
        status, body = _post(port, "/hook/ci", b'{"action":"opened"}', {"X-GitHub-Event": "pull_request"})
        assert status == 200 and body.get("ok") and "skipped" not in body
    finally:
        srv.shutdown()


def test_webhook_delivers_to_outbox_and_honors_silent(monkeypatch, tmp_path):
    from aegis.gateway.queue import DeliveryQueue
    cfg, store, make_handler = _webhook_server(monkeypatch, tmp_path)

    # non-silent reply -> exactly one outbox row to telegram:42
    _fake_agent(monkeypatch, reply="done")
    store.add("d1", "go", deliver="telegram:42")
    srv, port = _serve(make_handler, cfg, store)
    try:
        assert _post(port, "/hook/d1")[0] == 200
    finally:
        srv.shutdown()
    rows = DeliveryQueue().due()
    assert len(rows) == 1 and rows[0]["platform"] == "telegram" and rows[0]["chat_id"] == "42"

    # [SILENT] reply -> nothing new enqueued
    before = len(DeliveryQueue().due())
    _fake_agent(monkeypatch, reply="[SILENT] no change")
    store.add("d2", "go", deliver="telegram:99")
    srv, port = _serve(make_handler, cfg, store)
    try:
        assert _post(port, "/hook/d2")[0] == 200
    finally:
        srv.shutdown()
    assert len(DeliveryQueue().due()) == before          # silent -> no delivery


def test_webhook_prepends_skills(monkeypatch, tmp_path):
    cfg, store, make_handler = _webhook_server(monkeypatch, tmp_path)
    seen = _fake_agent(monkeypatch)
    store.add("s", "do it", skills=["github-review"])
    srv, port = _serve(make_handler, cfg, store)
    try:
        _post(port, "/hook/s")
    finally:
        srv.shutdown()
    assert "Load these skills first: github-review" in seen["prompt"]


# --- TASK 2: cron script / skills / multi-deliver / [SILENT] ----------------
def _fake_cron_agent(monkeypatch, reply):
    import aegis.agent.agent as am
    seen = {}

    class A:
        permissions = type("P", (), {"_mode_override": None})()
        def run(self, prompt):
            seen["prompt"] = prompt
            return type("R", (), {"content": reply})()
    monkeypatch.setattr(am.Agent, "create", staticmethod(lambda cfg, session=None: A()))
    return seen


def test_cron_silent_skips_sink(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.cron import CronStore, tick
    _fake_cron_agent(monkeypatch, reply="[SILENT] nothing")
    script = tmp_path / "mon.py"
    script.write_text("print('NO_CHANGE')")
    s = CronStore()
    s.add("every 1s", "report if changed", script=str(script), deliver="telegram:1")
    calls = []
    tick(None, sink=lambda ch, txt: calls.append((ch, txt)), store=s, verbose=False)
    assert calls == []                                   # silent -> zero sink calls


def test_cron_multi_deliver(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.cron import CronStore, tick
    _fake_cron_agent(monkeypatch, reply="here is the update")
    s = CronStore()
    s.add("every 1s", "summarize", deliver="telegram:1,discord:2")
    calls = []
    tick(None, sink=lambda ch, txt: calls.append(ch), store=s, verbose=False)
    assert calls == ["telegram:1", "discord:2"]          # one sink call per target


def test_cron_sets_delivery_target_as_agent_context(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.agent.agent as am
    from aegis.cron import CronStore, tick

    seen = {}

    class A:
        permissions = type("P", (), {"_mode_override": None})()
        platform = None
        chat_id = None
        def run(self, _prompt):
            seen["platform"] = self.platform
            seen["chat_id"] = self.chat_id
            return type("R", (), {"content": "[SILENT]"})()

    monkeypatch.setattr(am.Agent, "create", staticmethod(lambda cfg, session=None: A()))
    s = CronStore()
    s.add("every 1s", "send update", deliver="telegram:42")

    tick(None, sink=lambda _ch, _txt: None, store=s, verbose=False)

    assert seen == {"platform": "telegram", "chat_id": "42"}


def test_cron_script_context_in_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.cron import CronStore, tick
    seen = _fake_cron_agent(monkeypatch, reply="ok")
    script = tmp_path / "ctx.py"
    script.write_text("print('ARXIV: 3 new papers')")
    s = CronStore()
    s.add("every 1s", "summarize the context", script=str(script))
    tick(None, sink=lambda ch, txt: None, store=s, verbose=False)
    assert "ARXIV: 3 new papers" in seen["prompt"] and "# Context" in seen["prompt"]
