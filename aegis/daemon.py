"""User-level service helpers for the AEGIS dashboard and gateway."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config as cfg
from .config import Config
from .util import atomic_write, ensure_dir


@dataclass
class ServiceResult:
    ok: bool
    message: str


def _unit_dir() -> Path:
    return ensure_dir(Path.home() / ".config" / "systemd" / "user")


def _aegis_bin() -> str:
    return shutil.which("aegis") or sys.argv[0]


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def systemd_available() -> bool:
    return shutil.which("systemctl") is not None and _systemctl("is-system-running").returncode in (
        0,
        1,
    )


def install_dashboard_service(config: Config, *, enable_now: bool = True) -> ServiceResult:
    if shutil.which("systemctl") is None:
        return ServiceResult(False, "systemctl not found")
    port = int(config.get("server.dashboard_port", 9119))
    host = config.get("server.dashboard_host", "127.0.0.1")
    unit = _unit_dir() / "aegis-dashboard.service"
    content = f"""[Unit]
Description=AEGIS local dashboard
After=network.target

[Service]
Type=simple
Environment=AEGIS_HOME={cfg.get_home()}
WorkingDirectory=%h
ExecStart={_aegis_bin()} dashboard --host {host} --port {port}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""
    atomic_write(unit, content)
    reload_res = _systemctl("daemon-reload")
    if reload_res.returncode != 0:
        return ServiceResult(False, reload_res.stderr.strip() or "systemd daemon-reload failed")
    if enable_now:
        res = _systemctl("enable", "--now", unit.name)
        if res.returncode != 0:
            return ServiceResult(False, res.stderr.strip() or "systemd enable failed")
    return ServiceResult(True, f"{unit.name} installed")


def install_gateway_service(config: Config, channels: list[str], *, enable_now: bool = True) -> ServiceResult:
    if shutil.which("systemctl") is None:
        return ServiceResult(False, "systemctl not found")
    if not channels:
        return ServiceResult(False, "no gateway channels configured")
    unit = _unit_dir() / "aegis-gateway.service"
    channel_arg = ",".join(channels)
    content = f"""[Unit]
Description=AEGIS multi-channel gateway
After=network.target

[Service]
Type=simple
Environment=AEGIS_HOME={cfg.get_home()}
WorkingDirectory=%h
ExecStart={_aegis_bin()} gateway --channels {channel_arg}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    atomic_write(unit, content)
    reload_res = _systemctl("daemon-reload")
    if reload_res.returncode != 0:
        return ServiceResult(False, reload_res.stderr.strip() or "systemd daemon-reload failed")
    if enable_now:
        res = _systemctl("enable", "--now", unit.name)
        if res.returncode != 0:
            return ServiceResult(False, res.stderr.strip() or "systemd enable failed")
    return ServiceResult(True, f"{unit.name} installed")


def status() -> dict[str, str]:
    out: dict[str, str] = {}
    for unit in ("aegis-dashboard.service", "aegis-gateway.service"):
        res = _systemctl("is-active", unit)
        out[unit] = res.stdout.strip() or "unknown"
    return out


def cmd_daemon(args, config: Config) -> int:
    action = getattr(args, "action", "status")
    if action == "install":
        channels = [c.strip() for c in (getattr(args, "channels", None) or "").split(",") if c.strip()]
        if not channels:
            channels = list(config.get("gateway.channels", []) or [])
        dash = install_dashboard_service(config, enable_now=not getattr(args, "no_start", False))
        print(("✓ " if dash.ok else "! ") + dash.message)
        if channels:
            gate = install_gateway_service(config, channels, enable_now=not getattr(args, "no_start", False))
            print(("✓ " if gate.ok else "! ") + gate.message)
        return 0 if dash.ok else 1
    if action in ("start", "stop", "restart"):
        rc = 0
        for unit in ("aegis-dashboard.service", "aegis-gateway.service"):
            res = _systemctl(action, unit)
            print(f"{unit}: {res.stdout.strip() or res.stderr.strip() or action}")
            rc = rc or res.returncode
        return rc
    if action == "remove":
        _systemctl("disable", "--now", "aegis-dashboard.service")
        _systemctl("disable", "--now", "aegis-gateway.service")
        for unit in (_unit_dir() / "aegis-dashboard.service", _unit_dir() / "aegis-gateway.service"):
            try:
                unit.unlink()
                print(f"removed {unit.name}")
            except FileNotFoundError:
                pass
        _systemctl("daemon-reload")
        return 0
    for unit, state in status().items():
        print(f"{unit}: {state}")
    return 0
