from __future__ import annotations


class BrokenWriter:
    encoding = "utf-8"

    def write(self, _data):
        raise OSError("closed pipe")

    def flush(self):
        raise ValueError("closed")

    def fileno(self):
        return 1

    def isatty(self):
        raise OSError("no tty")


def test_safe_writer_swallows_broken_stdio_errors():
    from aegis.agent.process_bootstrap import _SafeWriter

    writer = _SafeWriter(BrokenWriter())

    assert writer.write("hello") == len("hello")
    assert writer.flush() is None
    assert writer.isatty() is False
    assert writer.encoding == "utf-8"


def test_install_safe_stdio_is_idempotent(monkeypatch):
    from aegis.agent import process_bootstrap

    stdout = BrokenWriter()
    stderr = BrokenWriter()
    monkeypatch.setattr(process_bootstrap.sys, "stdout", stdout)
    monkeypatch.setattr(process_bootstrap.sys, "stderr", stderr)

    process_bootstrap._install_safe_stdio()
    first_out = process_bootstrap.sys.stdout
    first_err = process_bootstrap.sys.stderr
    process_bootstrap._install_safe_stdio()

    assert process_bootstrap.sys.stdout is first_out
    assert process_bootstrap.sys.stderr is first_err
    assert isinstance(first_out, process_bootstrap._SafeWriter)
    assert isinstance(first_err, process_bootstrap._SafeWriter)


def test_proxy_resolution_honors_env_priority_and_no_proxy(monkeypatch):
    from aegis.agent.process_bootstrap import _get_proxy_for_base_url, _get_proxy_from_env

    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "proxy.local:8080")
    monkeypatch.setenv("HTTPS_PROXY", "https://secure-proxy.local:8443")

    assert _get_proxy_from_env() == "https://secure-proxy.local:8443"
    assert _get_proxy_for_base_url("https://api.example.test/v1") == "https://secure-proxy.local:8443"

    monkeypatch.setenv("NO_PROXY", "api.example.test")
    assert _get_proxy_for_base_url("https://api.example.test/v1") is None

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    assert _get_proxy_from_env() == "http://proxy.local:8080"
