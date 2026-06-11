"""User-level service helpers for the AEGIS dashboard, gateway, and cron runner."""

from __future__ import annotations

import shutil
import socket
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


def _unit_state(unit: str) -> str:
    if shutil.which("systemctl") is None:
        return "systemctl not found"
    res = _systemctl(
        "show",
        unit,
        "--property=LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainStatus",
    )
    if res.returncode != 0:
        return res.stderr.strip() or "unknown"
    values: dict[str, str] = {}
    for line in res.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value.strip()
    active = values.get("ActiveState") or "unknown"
    enabled = values.get("UnitFileState") or "unknown"
    sub = values.get("SubState") or "unknown"
    result = values.get("Result") or "success"
    exit_status = values.get("ExecMainStatus") or "0"
    summary = f"{active} ({sub}, {enabled})"
    if active == "failed" or result not in ("", "success") or exit_status not in ("", "0"):
        summary += (
            f" result={result or 'unknown'} exit={exit_status}; "
            f"hint: journalctl --user -u {unit} -n 20 --no-pager"
        )
    return summary


def _failed_after_start(unit: str) -> str:
    state = _unit_state(unit)
    return state if state.startswith("failed") or " result=" in state else ""


def systemd_available() -> bool:
    return shutil.which("systemctl") is not None and _systemctl("is-system-running").returncode in (
        0,
        1,
    )


def port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False


def install_dashboard_service(config: Config, *, enable_now: bool = True) -> ServiceResult:
    if shutil.which("systemctl") is None:
        return ServiceResult(False, "systemctl not found")
    port = int(config.get("server.dashboard_port", 9119))
    host = config.get("server.dashboard_host", "127.0.0.1")
    if enable_now and not port_available(host, port):
        return ServiceResult(False, f"dashboard port {host}:{port} is already in use")
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
        failed = _failed_after_start(unit.name)
        if failed:
            return ServiceResult(False, f"{unit.name} installed but failed: {failed}")
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
        failed = _failed_after_start(unit.name)
        if failed:
            return ServiceResult(False, f"{unit.name} installed but failed: {failed}")
    return ServiceResult(True, f"{unit.name} installed")


def install_cron_service(config: Config, *, enable_now: bool = True) -> ServiceResult:
    if shutil.which("systemctl") is None:
        return ServiceResult(False, "systemctl not found")
    unit = _unit_dir() / "aegis-cron.service"
    content = f"""[Unit]
Description=AEGIS cron scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=AEGIS_HOME={cfg.get_home()}
WorkingDirectory=%h
ExecStart={_aegis_bin()} cron run
Restart=on-failure
RestartSec=10

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
        failed = _failed_after_start(unit.name)
        if failed:
            return ServiceResult(False, f"{unit.name} installed but failed: {failed}")
    return ServiceResult(True, f"{unit.name} installed")


def cron_service_status() -> str:
    if not systemd_available():
        return "user systemd unavailable"
    return _unit_state("aegis-cron.service")


def control_cron_service(action: str) -> ServiceResult:
    if action not in {"start", "stop", "restart"}:
        return ServiceResult(False, f"unknown cron service action: {action}")
    if shutil.which("systemctl") is None:
        return ServiceResult(False, "systemctl not found")
    res = _systemctl(action, "aegis-cron.service")
    ok = res.returncode == 0
    return ServiceResult(ok, res.stdout.strip() or res.stderr.strip() or action)


def remove_cron_service() -> ServiceResult:
    if shutil.which("systemctl"):
        _systemctl("disable", "--now", "aegis-cron.service")
    try:
        (_unit_dir() / "aegis-cron.service").unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    if shutil.which("systemctl"):
        _systemctl("daemon-reload")
    return ServiceResult(True, "aegis-cron.service removed" if removed else "aegis-cron.service not installed")


def status() -> dict[str, str]:
    out: dict[str, str] = {}
    if not systemd_available():
        return {
            "aegis-dashboard.service": "user systemd unavailable",
            "aegis-gateway.service": "user systemd unavailable",
            "aegis-cron.service": "user systemd unavailable",
        }
    for unit in ("aegis-dashboard.service", "aegis-gateway.service", "aegis-cron.service"):
        out[unit] = _unit_state(unit)
    return out


def cmd_daemon(args, config: Config) -> int:
    action = getattr(args, "action", "status")
    if action == "install":
        channels = [c.strip() for c in (getattr(args, "channels", None) or "").split(",") if c.strip()]
        if not channels:
            channels = list(config.get("gateway.channels", []) or [])
        rc = 0
        dash = install_dashboard_service(config, enable_now=not getattr(args, "no_start", False))
        print(("✓ " if dash.ok else "! ") + dash.message)
        if not dash.ok:
            rc = 1
        if channels:
            gate = install_gateway_service(config, channels, enable_now=not getattr(args, "no_start", False))
            print(("✓ " if gate.ok else "! ") + gate.message)
            if not gate.ok:
                rc = 1
        return rc
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
