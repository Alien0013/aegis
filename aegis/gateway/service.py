"""Run the gateway as an OS-managed service (systemd user unit on Linux, launchd on macOS),
so it starts on boot and restarts on crash. Falls back to a clear message on unsupported OSes.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

_SYSTEMD_UNIT = "aegis-gateway.service"
_LAUNCHD_LABEL = "com.aegis.gateway"
_WINDOWS_TASK = "AEGIS_Gateway"
_WINDOWS_STARTUP_SCRIPT = "aegis-gateway.cmd"


def _aegis_bin() -> str:
    return shutil.which("aegis") or "aegis"


def _systemd_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_UNIT


def _launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _windows_startup_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / _WINDOWS_STARTUP_SCRIPT


def _gateway_command(channels: str) -> list[str]:
    return [_aegis_bin(), "gateway", "--channels", channels]


def _windows_task_installed() -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", _WINDOWS_TASK],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _windows_autostart_installed() -> bool:
    return _windows_task_installed() or _windows_startup_path().exists()


def _windows_gateway_pid() -> int | None:
    """Best-effort PID lookup for a running Windows gateway process."""
    queries = [
        [
            "wmic",
            "process",
            "where",
            "CommandLine like '%aegis%gateway%'",
            "get",
            "ProcessId,CommandLine",
            "/FORMAT:LIST",
        ],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -match 'aegis' -and $_.CommandLine -match 'gateway' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId",
        ],
    ]
    for argv in queries:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0:
            continue
        for line in (r.stdout or "").splitlines():
            text = line.strip()
            if not text:
                continue
            if text.lower().startswith("processid="):
                text = text.split("=", 1)[1].strip()
            try:
                pid = int(text)
            except ValueError:
                continue
            if pid > 0:
                return pid
    return None


def _systemd_main_pid() -> int | None:
    if shutil.which("systemctl") is None:
        return None
    res = subprocess.run(
        ["systemctl", "--user", "show", _SYSTEMD_UNIT, "--property=MainPID", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        pid = int((res.stdout or "").strip() or "0")
    except ValueError:
        return None
    return pid if pid > 0 else None


def _mark_planned_stop(pid: int | None) -> None:
    if not pid:
        return
    try:
        from .status import write_planned_stop_marker

        write_planned_stop_marker(pid)
    except Exception:  # noqa: BLE001
        pass


def install(channels: str = "telegram") -> str:
    """Install + start the gateway as a user service. Returns a status line."""
    system = platform.system()
    bin_ = _aegis_bin()
    if system == "Linux":
        unit = (
            "[Unit]\nDescription=AEGIS gateway\nAfter=network-online.target\n\n"
            f"[Service]\nExecStart={bin_} gateway --channels {channels}\n"
            "Restart=on-failure\nRestartSec=5\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        p = _systemd_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(unit)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        r = subprocess.run(["systemctl", "--user", "enable", "--now", _SYSTEMD_UNIT], check=False)
        return (f"installed systemd user service → {p}\n"
                "  manage: systemctl --user status|restart|stop aegis-gateway\n"
                "  (boot-persistence: run `loginctl enable-linger $USER` once)") \
            if r.returncode == 0 else f"wrote {p}, but `systemctl --user enable` failed (is systemd available?)"
    if system == "Darwin":
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>\n'
            f"  <key>Label</key><string>{_LAUNCHD_LABEL}</string>\n"
            f"  <key>ProgramArguments</key><array><string>{bin_}</string>"
            f"<string>gateway</string><string>--channels</string><string>{channels}</string></array>\n"
            "  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n</dict></plist>\n"
        )
        p = _launchd_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plist)
        subprocess.run(["launchctl", "unload", str(p)], check=False,
                       stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "load", str(p)], check=False)
        return (f"installed launchd agent → {p}\n"
                f"  manage: launchctl unload|load {p}")
    if system == "Windows":
        command = subprocess.list2cmdline(_gateway_command(channels))
        task = subprocess.run(
            ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", _WINDOWS_TASK, "/TR", command, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if task.returncode == 0:
            subprocess.run(["schtasks", "/Run", "/TN", _WINDOWS_TASK], check=False)
            return (f"installed Windows Scheduled Task → {_WINDOWS_TASK}\n"
                    f"  manage: schtasks /Query|/Run|/End /TN {_WINDOWS_TASK}")
        p = _windows_startup_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"@echo off\r\nstart \"AEGIS Gateway\" {command}\r\n", encoding="utf-8")
        try:
            subprocess.Popen(["cmd", "/c", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
        return (f"installed Windows Startup fallback → {p}\n"
                "  Scheduled Task creation failed; the gateway will start on next login.")
    return f"service install isn't supported on {system}; run `aegis gateway` directly or use your own supervisor."


def uninstall() -> str:
    system = platform.system()
    if system == "Linux":
        _mark_planned_stop(_systemd_main_pid())
        subprocess.run(["systemctl", "--user", "disable", "--now", _SYSTEMD_UNIT], check=False)
        p = _systemd_path()
        if p.exists():
            p.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return "removed systemd user service."
    if system == "Darwin":
        p = _launchd_path()
        subprocess.run(["launchctl", "unload", str(p)], check=False, stderr=subprocess.DEVNULL)
        if p.exists():
            p.unlink()
        return "removed launchd agent."
    if system == "Windows":
        _mark_planned_stop(_windows_gateway_pid())
        subprocess.run(["schtasks", "/End", "/TN", _WINDOWS_TASK], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["schtasks", "/Delete", "/TN", _WINDOWS_TASK, "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p = _windows_startup_path()
        if p.exists():
            p.unlink()
        return "removed Windows gateway autostart."
    return f"nothing to remove on {system}."


def status() -> str:
    system = platform.system()
    if system == "Linux":
        r = subprocess.run(["systemctl", "--user", "is-active", _SYSTEMD_UNIT],
                           capture_output=True, text=True)
        return f"systemd: {r.stdout.strip() or 'unknown'}"
    if system == "Darwin":
        installed = _launchd_path().exists()
        return f"launchd: {'loaded' if installed else 'not installed'}"
    if system == "Windows":
        pid = _windows_gateway_pid()
        installed = _windows_autostart_installed()
        if pid:
            return f"windows: running pid {pid}"
        return f"windows: {'installed' if installed else 'not installed'}"
    return f"no service manager on {system}."


def start(channels: str = "telegram") -> bool:
    system = platform.system()
    if system == "Linux" and _systemd_path().exists():
        return subprocess.run(["systemctl", "--user", "start", _SYSTEMD_UNIT], check=False).returncode == 0
    if system == "Darwin" and _launchd_path().exists():
        return subprocess.run(["launchctl", "load", str(_launchd_path())], check=False).returncode == 0
    if system == "Windows":
        if _windows_task_installed():
            return subprocess.run(["schtasks", "/Run", "/TN", _WINDOWS_TASK], check=False).returncode == 0
        p = _windows_startup_path()
        if p.exists():
            try:
                subprocess.Popen(["cmd", "/c", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                return False
    return False


def stop() -> bool:
    system = platform.system()
    if system == "Linux" and _systemd_path().exists():
        _mark_planned_stop(_systemd_main_pid())
        return subprocess.run(["systemctl", "--user", "stop", _SYSTEMD_UNIT], check=False).returncode == 0
    if system == "Darwin" and _launchd_path().exists():
        return subprocess.run(["launchctl", "unload", str(_launchd_path())], check=False, stderr=subprocess.DEVNULL).returncode == 0
    if system == "Windows":
        pid = _windows_gateway_pid()
        _mark_planned_stop(pid)
        handled = False
        if _windows_task_installed():
            handled = subprocess.run(["schtasks", "/End", "/TN", _WINDOWS_TASK], check=False).returncode == 0
        if pid:
            handled = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False).returncode == 0 or handled
        return handled
    return False


def restart() -> bool:
    """Restart the running service (used after self-update). True if a manager handled it."""
    system = platform.system()
    if system == "Linux" and _systemd_path().exists():
        _mark_planned_stop(_systemd_main_pid())
        return subprocess.run(["systemctl", "--user", "restart", _SYSTEMD_UNIT],
                              check=False).returncode == 0
    if system == "Darwin" and _launchd_path().exists():
        p = str(_launchd_path())
        subprocess.run(["launchctl", "unload", p], check=False, stderr=subprocess.DEVNULL)
        return subprocess.run(["launchctl", "load", p], check=False).returncode == 0
    if system == "Windows" and _windows_autostart_installed():
        stop()
        return start()
    return False


def cmd_gateway_service(action: str, channels: str = "telegram") -> int:
    fn = {
        "install": lambda: install(channels),
        "uninstall": uninstall,
        "status": status,
        "start": lambda: "started gateway service" if start(channels) else "gateway service is not installed",
        "stop": lambda: "stopped gateway service" if stop() else "gateway service is not running",
        "restart": lambda: "restarted gateway service" if restart() else "gateway service is not installed",
    }.get(action)
    if not fn:
        print("usage: aegis gateway install|uninstall|status|start|stop|restart [--channels ...]")
        return 1
    print(fn())
    return 0


# self-update hook: after `aegis update`, bounce the service if one is installed
def restart_after_update() -> None:
    if restart():
        print("  ▸ gateway service restarted")
