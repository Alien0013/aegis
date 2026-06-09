"""Run the gateway as an OS-managed service (systemd user unit on Linux, launchd on macOS),
so it starts on boot and restarts on crash. Falls back to a clear message on unsupported OSes.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

_SYSTEMD_UNIT = "aegis-gateway.service"
_LAUNCHD_LABEL = "com.aegis.gateway"


def _aegis_bin() -> str:
    return shutil.which("aegis") or "aegis"


def _systemd_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_UNIT


def _launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


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
    return f"service install isn't supported on {system}; run `aegis gateway` directly or use your own supervisor."


def uninstall() -> str:
    system = platform.system()
    if system == "Linux":
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
    return f"no service manager on {system}."


def restart() -> bool:
    """Restart the running service (used after self-update). True if a manager handled it."""
    system = platform.system()
    if system == "Linux" and _systemd_path().exists():
        return subprocess.run(["systemctl", "--user", "restart", _SYSTEMD_UNIT],
                              check=False).returncode == 0
    if system == "Darwin" and _launchd_path().exists():
        p = str(_launchd_path())
        subprocess.run(["launchctl", "unload", p], check=False, stderr=subprocess.DEVNULL)
        return subprocess.run(["launchctl", "load", p], check=False).returncode == 0
    return False


def cmd_gateway_service(action: str, channels: str = "telegram") -> int:
    fn = {"install": lambda: install(channels), "uninstall": uninstall, "status": status}.get(action)
    if not fn:
        print("usage: aegis gateway install|uninstall|status [--channels ...]")
        return 1
    print(fn())
    return 0


# self-update hook: after `aegis update`, bounce the service if one is installed
def restart_after_update() -> None:
    if restart():
        print("  ▸ gateway service restarted")
