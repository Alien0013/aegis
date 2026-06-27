"""AEGIS-native install/update surface ledger and setup plans.

Reference harnesses expose several one-file setup helpers. AEGIS keeps the real
behavior in native CLI commands and package modules; this ledger makes those
compatibility surfaces explicit without duplicating installer logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


INSTALL_SURFACE_REMAPS: dict[str, str] = {
    "agent/lsp/install.py": "aegis/lsp/install.py",
    "agent/process_bootstrap.py": "aegis/agent/process_bootstrap.py",
    "setup/bootstrap.py": "apps/bootstrap-installer/package.json",
    "setup/postinstall.py": "aegis/cli/main.py::postinstall",
    "setup/update.py": "aegis/cli/main.py::cmd_update",
    "setup/uninstall.py": "aegis/cli/main.py::cmd_uninstall",
    "setup/setup-whatsapp-cloud.py": "aegis/platforms/helpers.py::whatsapp_cloud",
    "scripts/lib/node-bootstrap.sh": "package.json::engines.node",
    "setup.py": "pyproject.toml",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _path_part(target: str) -> str:
    return target.split("::", 1)[0]


def _target_exists(root: Path, target: str) -> bool:
    path = root / _path_part(target)
    if not path.exists():
        return False
    marker = target.split("::", 1)[1] if "::" in target else ""
    if not marker:
        return True
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    if target == "package.json::engines.node":
        return '"engines"' in text and '"node"' in text
    return marker in text


def audit_install_surface_parity(root: str | Path | None = None) -> list[str]:
    """Return missing compatibility wrappers or native AEGIS remap targets."""

    repo = Path(root).resolve() if root is not None else _repo_root()
    missing: list[str] = []
    for compat_path, native_target in INSTALL_SURFACE_REMAPS.items():
        if not (repo / compat_path).exists():
            missing.append(f"missing compatibility surface: {compat_path}")
        if not _target_exists(repo, native_target):
            missing.append(f"missing native AEGIS target: {native_target}")
    return missing


def bootstrap_plan(root: str | Path | None = None) -> dict[str, Any]:
    """Describe the repository-native installer/onboarding entry points."""

    repo = Path(root).resolve() if root is not None else _repo_root()
    return {
        "object": "aegis.install_surface.bootstrap_plan",
        "product": "AEGIS",
        "repository": str(repo),
        "installers": ["install.sh", "install.ps1", "scripts/install.sh", "scripts/install.ps1"],
        "bootstrap_ui": "apps/bootstrap-installer",
        "commands": {
            "setup": "aegis setup",
            "postinstall": "aegis postinstall",
            "update": "aegis update",
            "uninstall": "aegis uninstall --purge",
            "whatsapp_cloud_setup": "aegis setup-whatsapp-cloud",
        },
        "node": {
            "required_for": ["web development", "desktop packaging", "TUI bundle rebuilds"],
            "not_required_for": ["pip install", "core CLI runtime"],
            "engine": ">=20.0.0",
            "helper": "scripts/lib/node-bootstrap.sh",
        },
        "audit_missing": audit_install_surface_parity(repo),
    }


def whatsapp_cloud_setup_plan(*, dry_run: bool = True) -> dict[str, Any]:
    """Return the native WhatsApp Cloud bridge setup plan without touching the network."""

    from .platforms import BRIDGE_PLATFORM_DEFINITIONS

    row = BRIDGE_PLATFORM_DEFINITIONS["whatsapp_cloud"]
    env_prefix = str(row["env_prefix"])
    default_port = int(row.get("default_port") or 18801)
    return {
        "object": "aegis.whatsapp_cloud.setup_plan",
        "product": "AEGIS",
        "dry_run": bool(dry_run),
        "mutates": not bool(dry_run),
        "channel": "whatsapp_cloud",
        "display_name": str(row.get("display_name") or "WhatsApp Cloud"),
        "env_prefix": env_prefix,
        "default_port": default_port,
        "webhook_url": f"http://127.0.0.1:{default_port}/in",
        "config_key": "gateway.channels",
        "required_env": [f"{env_prefix}_SECRET", f"{env_prefix}_VERIFY_TOKEN"],
        "optional_env": [
            f"{env_prefix}_HOST",
            f"{env_prefix}_PORT",
            f"{env_prefix}_ALLOW_UNSIGNED_LOOPBACK",
        ],
        "commands": [
            "aegis setup-whatsapp-cloud",
            "aegis gateway setup --channels whatsapp_cloud",
            "aegis gateway --channels whatsapp_cloud",
        ],
        "notes": [
            "AEGIS uses the native webhook bridge for WhatsApp Cloud ingress.",
            "Keep the bridge bound to loopback unless you put it behind your own HTTPS/auth layer.",
        ],
    }


def print_bootstrap_plan(*, json_output: bool = False, root: str | Path | None = None) -> None:
    plan = bootstrap_plan(root)
    if json_output:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return
    print("AEGIS bootstrap surfaces")
    print(f"  repository: {plan['repository']}")
    print("  installers: " + ", ".join(plan["installers"]))
    print("  bootstrap UI: " + str(plan["bootstrap_ui"]))
    print("  commands:")
    for name, command in plan["commands"].items():
        print(f"    {name}: {command}")
    if plan["audit_missing"]:
        print("  missing:")
        for item in plan["audit_missing"]:
            print(f"    - {item}")


__all__ = [
    "INSTALL_SURFACE_REMAPS",
    "audit_install_surface_parity",
    "bootstrap_plan",
    "print_bootstrap_plan",
    "whatsapp_cloud_setup_plan",
]
