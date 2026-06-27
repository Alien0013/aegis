from __future__ import annotations


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"ok": true}'


def test_webhook_subscribe_alias_accepts_prompt_option(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    from aegis.webhook import WebhookStore

    assert main([
        "webhook",
        "subscribe",
        "ci",
        "--prompt",
        "summarize build",
        "--secret",
        "secret",
        "--events",
        "push,pull_request",
        "--skills",
        "github-review",
    ]) == 0
    out = capsys.readouterr().out

    assert "added webhook 'ci'" in out
    hook = WebhookStore().get("ci")
    assert hook is not None
    assert hook.prompt == "summarize build"
    assert hook.secret == "secret"
    assert hook.events == ["push", "pull_request"]
    assert hook.skills == ["github-review"]


def test_webhook_test_sends_signed_payload(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    import urllib.request

    from aegis.cli.main import main
    from aegis.webhook import WebhookStore

    WebhookStore().add("ci", "go", secret="secret")
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["data"] = req.data
        seen["headers"] = dict(req.headers)
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert main(["webhook", "test", "ci", "--payload", '{"ok":true}']) == 0
    out = capsys.readouterr().out

    assert "Sending test POST" in out
    assert "Response (200)" in out
    assert seen["url"].endswith("/hook/ci")
    assert seen["data"] == b'{"ok":true}'
    assert seen["timeout"] == 10
    assert seen["headers"]["X-hub-signature-256"].startswith("sha256=")
