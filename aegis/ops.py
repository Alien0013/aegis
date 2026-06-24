"""Operational commands: security audit, debug report, and a Bitwarden secrets sync."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from typing import Any

from . import config as cfg
from .redact import redact_secret_values, redact_secrets
from .util import read_text


def _scan(text: str, command: bool = False) -> tuple[bool, str]:
    try:
        from .security_scan import scan_command, scan_text
        return scan_command(text) if command else scan_text(text)
    except Exception:  # noqa: BLE001
        return False, ""


def security_audit_report(config, *, include_dependencies: bool = True) -> dict[str, Any]:
    """Return a redacted, machine-readable security audit report."""
    findings: list[dict[str, Any]] = []
    dependency: dict[str, Any] = {"available": False, "status": "skipped", "output": ""}
    if include_dependencies and shutil.which("pip-audit"):
        r = subprocess.run(["pip-audit", "--format", "columns"], capture_output=True, text=True)
        output = redact_secrets((r.stdout.strip() or r.stderr.strip())[:20_000])
        dependency = {
            "available": True,
            "status": "ok" if r.returncode == 0 else "findings",
            "returncode": r.returncode,
            "output": output,
        }
        if r.returncode != 0:
            findings.append({
                "surface": "dependencies",
                "name": "pip-audit",
                "severity": "warning",
                "reason": "pip-audit reported dependency findings",
            })

    for name, spec in (config.get("mcp.servers", {}) or {}).items():
        if not isinstance(spec, dict):
            continue
        line = (str(spec.get("command", "")) + " " + " ".join(spec.get("args", []))).strip()
        sus, why = _scan(line, command=True)
        if sus:
            findings.append({
                "surface": "mcp",
                "name": str(name),
                "severity": "warning",
                "reason": why,
                "preview": redact_secrets(line[:500]),
            })

    pdir = cfg.sub("plugins")
    if pdir.exists():
        for f in pdir.rglob("*.py"):
            sus, why = _scan(read_text(f))
            if sus:
                findings.append({
                    "surface": "plugin",
                    "name": f.name,
                    "path": str(f),
                    "severity": "warning",
                    "reason": why,
                })

    skills_dir = cfg.skills_dir()
    if skills_dir.exists():
        for md in skills_dir.rglob("SKILL.md"):
            sus, why = _scan(read_text(md))
            if sus:
                findings.append({
                    "surface": "skill",
                    "name": md.parent.name,
                    "path": str(md),
                    "severity": "warning",
                    "reason": why,
                })

    critical = sum(1 for row in findings if row.get("severity") == "critical")
    warning = sum(1 for row in findings if row.get("severity") == "warning")
    return redact_secret_values({
        "ok": critical == 0,
        "summary": {
            "findings": len(findings),
            "critical": critical,
            "warning": warning,
        },
        "dependency_audit": dependency,
        "findings": findings,
    })


def _security_audit_markdown(report: dict[str, Any]) -> str:
    lines = ["# AEGIS security audit", ""]
    dep = report.get("dependency_audit") or {}
    if dep.get("available"):
        lines.extend([
            "## dependencies (pip-audit)",
            str(dep.get("output") or dep.get("status") or "ok"),
            "",
        ])
    else:
        lines.extend([
            "## dependencies",
            "install `pip-audit` for CVE scanning",
            "",
        ])
    lines.append("## findings")
    findings = report.get("findings") or []
    if findings:
        for item in findings:
            lines.append(
                f"- {item.get('severity', 'warning')}: {item.get('surface')}/{item.get('name')}: "
                f"{item.get('reason', '')}"
            )
    else:
        lines.append("- no suspicious MCP commands, plugins, or skills found")
    return "\n".join(lines).rstrip() + "\n"


def cmd_security_audit(args, config) -> int:
    """`aegis security audit` — scan deps, MCP servers, plugins, and skills."""
    report = security_audit_report(config)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_security_audit_markdown(report), end="")
    if getattr(args, "fail_on", None) == "any" and report.get("findings"):
        return 1
    return 0


def cmd_debug(args, config) -> int:
    """`aegis debug share` — bundle redacted logs + config + doctor output into a zip."""

    def env_keys(text: str) -> str:
        rows: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            key = re.sub(r"^export\s+", "", key).strip()
            if key:
                rows.append(f"{key}=<redacted>")
        return "\n".join(rows)

    def redacted_config_yaml(raw: str) -> str:
        import yaml

        try:
            data = yaml.safe_load(raw) if raw.strip() else {}
        except yaml.YAMLError:
            return redact_secrets(raw)
        if isinstance(data, dict):
            return yaml.safe_dump(redact_secret_values(data), sort_keys=False)
        return redact_secrets(raw)

    out = cfg.sub("debug-report.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # redacted config
        raw = read_text(cfg.config_path())
        redacted_config = redacted_config_yaml(raw)
        z.writestr("config.yaml", redacted_config)
        z.writestr("config.redacted.yaml", redacted_config)
        # .env keys only (values redacted)
        z.writestr("env.keys.txt", env_keys(read_text(cfg.env_path())))
        auth = read_text(cfg.auth_path())
        if auth.strip():
            z.writestr("auth.redacted.json", redact_secrets(auth))
        # logs
        logs = cfg.logs_dir()
        if logs.exists():
            for f in logs.glob("*"):
                if f.is_file():
                    z.writestr(f"logs/{f.name}", redact_secrets(read_text(f)[-50_000:]))
        # doctor summary
        import io
        import contextlib
        from .cli.main import cmd_doctor
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_doctor(type("A", (), {"fix": False})(), config)
        z.writestr("doctor.txt", redact_secrets(buf.getvalue()))
    print(f"wrote debug report → {out}")
    print("Secrets are redacted. Attach this file when reporting an issue.")
    return 0


def cmd_secrets(args, config) -> int:
    """`aegis secrets bitwarden` — pull API keys from Bitwarden into ~/.aegis/.env via the `bw` CLI."""
    if getattr(args, "provider", None) != "bitwarden":
        print("usage: aegis secrets bitwarden   (requires the `bw` CLI, logged in + unlocked)")
        return 1
    if not shutil.which("bw"):
        print("Bitwarden CLI `bw` not found. Install it and run `bw login && bw unlock`.")
        return 1
    r = subprocess.run(["bw", "list", "items"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"bw error (is the vault unlocked? export BW_SESSION): {r.stderr.strip()[:200]}")
        return 1
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        print("could not parse `bw list items` output")
        return 1
    count = 0
    for item in items:
        # treat any custom field whose name is UPPER_SNAKE as an env secret
        for field in item.get("fields", []) or []:
            key = (field.get("name") or "").strip()
            val = field.get("value") or ""
            if key.isupper() and "_" in key and val:
                cfg.set_env_var(key, val)
                count += 1
    print(f"synced {count} secret(s) from Bitwarden into {cfg.env_path()}")
    return 0
