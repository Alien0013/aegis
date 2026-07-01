from __future__ import annotations

from types import SimpleNamespace


class _Stream:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


def test_windows_utf8_bootstrap_is_noop_on_posix(monkeypatch):
    from aegis import bootstrap

    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap, "_stdio_bootstrap_applied", False)

    assert bootstrap.apply_windows_utf8_stdio() is False


def test_windows_utf8_bootstrap_sets_env_and_reconfigures_streams(monkeypatch):
    from aegis import bootstrap

    stdout = _Stream()
    stderr = _Stream()
    stdin = _Stream()
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.setattr(bootstrap.sys, "stdout", stdout)
    monkeypatch.setattr(bootstrap.sys, "stderr", stderr)
    monkeypatch.setattr(bootstrap.sys, "stdin", stdin)
    monkeypatch.setattr(bootstrap, "_stdio_bootstrap_applied", False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    assert bootstrap.apply_windows_utf8_stdio() is True
    assert bootstrap.os.environ["PYTHONUTF8"] == "1"
    assert bootstrap.os.environ["PYTHONIOENCODING"] == "utf-8"
    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stdin.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_windows_utf8_bootstrap_is_idempotent(monkeypatch):
    from aegis import bootstrap

    stdout = _Stream()
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.setattr(bootstrap.sys, "stdout", stdout)
    monkeypatch.setattr(bootstrap.sys, "stderr", SimpleNamespace())
    monkeypatch.setattr(bootstrap.sys, "stdin", SimpleNamespace())
    monkeypatch.setattr(bootstrap, "_stdio_bootstrap_applied", False)

    assert bootstrap.apply_windows_utf8_stdio() is True
    assert bootstrap.apply_windows_utf8_stdio() is False
    assert len(stdout.calls) == 1
