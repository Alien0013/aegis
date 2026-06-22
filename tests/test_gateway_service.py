"""Gateway service manager parity tests."""

from __future__ import annotations


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_windows_gateway_service_install_uses_scheduled_task(tmp_path, monkeypatch):
    from aegis.gateway import service

    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "_aegis_bin", lambda: r"C:\Aegis\aegis.exe")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return _Result(returncode=0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    message = service.install("telegram,discord")

    assert "Scheduled Task" in message
    assert calls[0][:4] == ["schtasks", "/Create", "/SC", "ONLOGON"]
    assert "/TN" in calls[0] and service._WINDOWS_TASK in calls[0]
    assert calls[0][calls[0].index("/TR") + 1] == r'C:\Aegis\aegis.exe gateway --channels telegram,discord'
    assert calls[1] == ["schtasks", "/Run", "/TN", service._WINDOWS_TASK]


def test_windows_gateway_service_install_startup_fallback(tmp_path, monkeypatch):
    from aegis.gateway import service

    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "_aegis_bin", lambda: r"C:\Aegis\aegis.exe")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    popen_calls = []

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["schtasks", "/Create"]:
            return _Result(returncode=1)
        return _Result(returncode=0)

    class FakePopen:
        def __init__(self, argv, **_kwargs):
            popen_calls.append(argv)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    monkeypatch.setattr(service.subprocess, "Popen", FakePopen)

    message = service.install("telegram")
    script = service._windows_startup_path()

    assert "Startup fallback" in message
    assert script.exists()
    assert "aegis.exe gateway --channels telegram" in script.read_text(encoding="utf-8")
    assert popen_calls == [["cmd", "/c", str(script)]]


def test_windows_gateway_service_stop_marks_planned_stop_and_kills_pid(monkeypatch):
    from aegis.gateway import service

    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "_windows_gateway_pid", lambda: 4321)
    monkeypatch.setattr(service, "_windows_task_installed", lambda: True)
    marked = []
    calls = []

    monkeypatch.setattr(service, "_mark_planned_stop", lambda pid: marked.append(pid))

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return _Result(returncode=0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    assert service.stop() is True
    assert marked == [4321]
    assert ["schtasks", "/End", "/TN", service._WINDOWS_TASK] in calls
    assert ["taskkill", "/PID", "4321", "/T", "/F"] in calls


def test_windows_gateway_service_restart_starts_installed_autostart(monkeypatch):
    from aegis.gateway import service

    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "_windows_autostart_installed", lambda: True)
    calls = []
    monkeypatch.setattr(service, "stop", lambda: calls.append("stop") or True)
    monkeypatch.setattr(service, "start", lambda channels="telegram": calls.append(("start", channels)) or True)

    assert service.restart() is True
    assert calls == ["stop", ("start", "telegram")]
