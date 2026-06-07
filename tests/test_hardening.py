"""Regression tests for the audit fixes (security + robustness)."""

from __future__ import annotations


def test_hardline_rm_variants_all_blocked():
    from aegis.tools.permissions import is_hardline_blocked as h
    blocked = [
        "rm -rf /", "rm -rf --no-preserve-root /", "rm -r -f /",
        "rm --recursive --force /", "rm -fr ~", "sudo rm -rf /",
        "rm -rf /*", "rm -rf $HOME", "cd x && rm -rf /",
    ]
    for cmd in blocked:
        assert h({"command": cmd}), f"should block: {cmd}"
    # legitimate recursive removes are NOT hardline-blocked
    for ok in ["rm -rf build", "rm -rf ./node_modules", "rm -rf /tmp/scratch", "rm file.txt"]:
        assert not h({"command": ok}), f"should NOT block: {ok}"


def test_surrogate_sanitization():
    from aegis.agent import governance
    from aegis.types import Message
    bad = "hello \ud800 world"          # lone surrogate
    msgs = [Message.user(bad)]
    out = governance.normalize(msgs)
    assert "\ud800" not in out[0].content and "hello" in out[0].content
    # and it now JSON-encodes without error
    import json
    json.dumps(out[0].content).encode("utf-8")


def test_fts_cleaned_on_delete():
    from aegis.session import Session, SessionStore
    from aegis.types import Message
    st = SessionStore()
    s = Session.create()
    s.messages = [Message.user("unique-token-xyz kubernetes")]
    st.save(s)
    assert st.search_messages("unique-token-xyz")
    st.delete(s.id)
    assert not st.search_messages("unique-token-xyz")   # no orphan FTS rows


def test_sqlite_wal_enabled():
    from aegis.session import SessionStore
    st = SessionStore()
    with st._conn() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() in ("wal", "memory")


def test_provider_retries_transient(monkeypatch):
    from aegis.providers.base import Provider
    from aegis.types import LLMResponse

    class FlakyTransport:
        api_mode = None
        def __init__(self):
            self.calls = 0
        def complete(self, **kw):
            self.calls += 1
            if self.calls < 3:
                e = RuntimeError("boom"); e.status = 503  # transient
                raise e
            return LLMResponse(text="ok")

    monkeypatch.setattr("time.sleep", lambda *_: None)   # don't actually wait
    t = FlakyTransport()
    p = Provider(name="x", transport=t, auth=None, base_url="http://x", model="m",
                 context_length=64000, api_mode=None)
    assert p.complete([]).text == "ok" and t.calls == 3
