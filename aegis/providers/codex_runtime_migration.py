"""Codex app-server config migration helpers.

When AEGIS delegates a turn to ``codex app-server``, Codex owns MCP/plugin
loading for that subprocess. This module writes an idempotent managed block to
``<CODEX_HOME>/config.toml`` so Codex can see AEGIS' configured MCP servers and
an ``aegis-tools`` callback server for capabilities Codex does not provide.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MIGRATION_MARKER = "# managed by aegis - `aegis codex-runtime migrate` regenerates this section"
MIGRATION_END_MARKER = "# end aegis managed section"


@dataclass
class CodexMigrationReport:
    target_path: Path | None = None
    migrated: list[str] = field(default_factory=list)
    skipped_keys_per_server: dict[str, list[str]] = field(default_factory=dict)
    migrated_plugins: list[str] = field(default_factory=list)
    plugin_query_error: str | None = None
    wrote_permissions_default: str | None = None
    errors: list[str] = field(default_factory=list)
    written: bool = False
    dry_run: bool = False

    def summary(self) -> str:
        lines: list[str] = []
        if self.dry_run:
            lines.append(f"(dry run) Would write {self.target_path}")
        elif self.written:
            lines.append(f"Wrote {self.target_path}")
        if self.migrated:
            lines.append(f"Migrated {len(self.migrated)} MCP server(s):")
            for name in self.migrated:
                skipped = self.skipped_keys_per_server.get(name, [])
                suffix = f" (skipped: {', '.join(skipped)})" if skipped else ""
                lines.append(f"  - {name}{suffix}")
        else:
            lines.append("No MCP servers found in AEGIS config.")
        if self.migrated_plugins:
            lines.append(f"Migrated {len(self.migrated_plugins)} native Codex plugin(s):")
            lines.extend(f"  - {name}" for name in self.migrated_plugins)
        elif self.plugin_query_error:
            lines.append(f"Codex plugin discovery skipped: {self.plugin_query_error}")
        if self.wrote_permissions_default:
            lines.append(f"Wrote default_permissions = {self.wrote_permissions_default!r}")
        lines.extend(f"! {err}" for err in self.errors)
        return "\n".join(lines)


_KNOWN_AEGIS_MCP_KEYS = {
    "command",
    "args",
    "env",
    "cwd",
    "url",
    "headers",
    "transport",
    "timeout",
    "connect_timeout",
    "enabled",
    "description",
}
_DROPPED_WITH_WARNING = {
    "auth",
    "oauth",
    "sampling",
    "tool_filter",
    "supports_parallel_tool_calls",
}


def _quote_key(key: str) -> str:
    if key and all(ch.isalnum() or ch in "-_" for ch in key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\b", "\\b")
            .replace("\t", "\\t")
            .replace("\n", "\\n")
            .replace("\f", "\\f")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_quote_key(str(k))} = {_format_toml_value(v)}"
            for k, v in value.items()
        )
        return "{ " + items + " }" if items else "{}"
    raise ValueError(f"Unsupported TOML value type: {type(value).__name__}")


def _translate_one_server(name: str, aegis_cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(aegis_cfg, dict):
        return None, []

    out: dict[str, Any] = {}
    skipped: list[str] = []
    has_command = bool(aegis_cfg.get("command"))
    has_url = bool(aegis_cfg.get("url"))
    if has_command and has_url:
        skipped.append("url (both command and url set; preferring stdio)")
        has_url = False

    if has_command:
        out["command"] = str(aegis_cfg["command"])
        args = aegis_cfg.get("args") or []
        if args:
            out["args"] = [str(arg) for arg in args]
        env = aegis_cfg.get("env") or {}
        if isinstance(env, dict) and env:
            out["env"] = {str(k): str(v) for k, v in env.items()}
        cwd = aegis_cfg.get("cwd")
        if cwd:
            out["cwd"] = str(cwd)
    elif has_url:
        out["url"] = str(aegis_cfg["url"])
        headers = aegis_cfg.get("headers") or {}
        if isinstance(headers, dict) and headers:
            out["http_headers"] = {str(k): str(v) for k, v in headers.items()}
        if str(aegis_cfg.get("transport") or "").lower() == "sse":
            skipped.append("transport=sse (codex auto-negotiates)")
    else:
        return None, ["no command or url field"]

    if "timeout" in aegis_cfg:
        try:
            out["tool_timeout_sec"] = float(aegis_cfg["timeout"])
        except (TypeError, ValueError):
            skipped.append("timeout (not numeric)")
    if "connect_timeout" in aegis_cfg:
        try:
            out["startup_timeout_sec"] = float(aegis_cfg["connect_timeout"])
        except (TypeError, ValueError):
            skipped.append("connect_timeout (not numeric)")
    if aegis_cfg.get("enabled") is False:
        out["enabled"] = False

    for key in aegis_cfg:
        if key in _DROPPED_WITH_WARNING:
            skipped.append(f"{key} (no codex equivalent)")
        elif key not in _KNOWN_AEGIS_MCP_KEYS:
            skipped.append(f"{key} (unknown AEGIS key)")
    return out, skipped


def render_codex_toml_section(
    servers: dict[str, dict[str, Any]],
    *,
    plugins: list[dict[str, Any]] | None = None,
    default_permission_profile: str | None = None,
) -> str:
    out = [MIGRATION_MARKER]
    if not servers and not plugins and not default_permission_profile:
        out.append("# (no MCP servers, plugins, or permissions configured by AEGIS)")
        out.append(MIGRATION_END_MARKER)
        return "\n".join(out) + "\n"

    if default_permission_profile:
        normalized = (
            default_permission_profile
            if default_permission_profile.startswith(":")
            else f":{default_permission_profile}"
        )
        out.append("")
        out.append(f"default_permissions = {_format_toml_value(normalized)}")

    for name in sorted(servers):
        out.append("")
        out.append(f"[mcp_servers.{_quote_key(name)}]")
        for key, value in servers[name].items():
            out.append(f"{_quote_key(str(key))} = {_format_toml_value(value)}")

    for plugin in sorted(plugins or [], key=lambda p: f"{p.get('name','')}@{p.get('marketplace','')}"):
        name = str(plugin.get("name") or "")
        if not name:
            continue
        marketplace = str(plugin.get("marketplace") or "openai-curated")
        qualified = f"{name}@{marketplace}"
        out.append("")
        out.append(f"[plugins.{_quote_key(qualified)}]")
        out.append(f"enabled = {_format_toml_value(bool(plugin.get('enabled', True)))}")

    out.append("")
    out.append(MIGRATION_END_MARKER)
    return "\n".join(out) + "\n"


def _looks_like_table_header(stripped_line: str) -> bool:
    if not stripped_line.startswith("["):
        return False
    head = stripped_line.split("#", 1)[0].rstrip()
    if not head.endswith("]"):
        return False
    bracket_idx = head.index("]")
    return "=" not in head[: bracket_idx + 1]


def _strip_existing_managed_block(toml_text: str) -> str:
    lines = toml_text.splitlines(keepends=True)
    out: list[str] = []
    in_managed = False
    saw_end_marker = False
    for line in lines:
        stripped_nl = line.rstrip("\n")
        if stripped_nl == MIGRATION_MARKER:
            in_managed = True
            saw_end_marker = False
            continue
        if in_managed:
            if stripped_nl == MIGRATION_END_MARKER:
                in_managed = False
                saw_end_marker = True
                continue
            stripped = line.lstrip()
            if not saw_end_marker and stripped.startswith("[") and not (
                stripped.startswith("[mcp_servers")
                or stripped.startswith("[plugins")
                or stripped.startswith("[permissions]")
                or stripped.startswith("[permissions.")
            ):
                in_managed = False
                out.append(line)
            continue
        out.append(line)
    return "".join(out)


def _strip_unmanaged_plugin_tables(toml_text: str) -> str:
    lines = toml_text.splitlines(keepends=True)
    out: list[str] = []
    in_plugin_table = False
    for line in lines:
        stripped = line.lstrip()
        if _looks_like_table_header(stripped):
            in_plugin_table = stripped.startswith("[plugins.")
            if in_plugin_table:
                continue
        if not in_plugin_table:
            out.append(line)
    return "".join(out)


def _insert_managed_block_at_top_level(user_text: str, managed_block: str) -> str:
    if not user_text.strip():
        return managed_block
    lines = user_text.splitlines(keepends=True)
    first_table_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("["):
            first_table_idx = idx
            break
    if first_table_idx is None:
        prefix = user_text.rstrip("\n")
        return f"{prefix}\n\n{managed_block}" if prefix else managed_block
    prefix = "".join(lines[:first_table_idx]).rstrip("\n")
    suffix = "".join(lines[first_table_idx:]).lstrip("\n")
    if prefix:
        return f"{prefix}\n\n{managed_block}\n{suffix}"
    return f"{managed_block}\n{suffix}"


def _looks_like_test_tempdir(path: str) -> bool:
    if not path:
        return False
    lowered = path.lower()
    needles = ("pytest-of-", "/pytest-", "/tmp/pytest", "/private/var/folders/")
    return any(needle in lowered for needle in needles)


def build_aegis_tools_mcp_entry() -> dict[str, Any]:
    import sys

    env: dict[str, str] = {}
    aegis_home = os.environ.get("AEGIS_HOME") or ""
    if aegis_home and _looks_like_test_tempdir(aegis_home):
        aegis_home = ""
    if aegis_home:
        env["AEGIS_HOME"] = aegis_home
    pythonpath = os.environ.get("PYTHONPATH")
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    env["AEGIS_QUIET"] = "1"
    env["AEGIS_REDACT_SECRETS"] = "true"

    return {
        "command": sys.executable,
        "args": ["-m", "aegis.mcp.aegis_tools_mcp_server"],
        "env": env,
        "startup_timeout_sec": 30.0,
        "tool_timeout_sec": 600.0,
    }


def _query_codex_plugins(codex_home: Path | None = None, timeout: float = 8.0) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from .codex_app_server import _CodexAppServerClient
    except Exception as exc:  # noqa: BLE001
        return [], f"transport unavailable: {exc}"

    old_codex_home = os.environ.get("CODEX_HOME")
    if codex_home is not None:
        os.environ["CODEX_HOME"] = str(codex_home)
    try:
        with _CodexAppServerClient() as client:
            client.initialize()
            resp = client.request("plugin/list", {}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return [], f"plugin/list query failed: {exc}"
    finally:
        if codex_home is not None:
            if old_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex_home

    plugins: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    marketplaces = resp.get("marketplaces") or []
    if not isinstance(marketplaces, list):
        return [], "plugin/list response missing 'marketplaces'"
    for marketplace in marketplaces:
        if not isinstance(marketplace, dict):
            continue
        market_name = str(marketplace.get("name") or "openai-curated")
        for plugin in marketplace.get("plugins") or []:
            if not isinstance(plugin, dict) or not plugin.get("installed"):
                continue
            availability = str(plugin.get("availability") or "").upper()
            if availability and availability != "AVAILABLE":
                continue
            name = str(plugin.get("name") or "")
            key = (name, market_name)
            if not name or key in seen:
                continue
            seen.add(key)
            plugins.append({
                "name": name,
                "marketplace": market_name,
                "enabled": bool(plugin.get("enabled", True)),
            })
    return plugins, None


def migrate(
    aegis_config: dict[str, Any],
    *,
    codex_home: Path | None = None,
    dry_run: bool = False,
    discover_plugins: bool = False,
    default_permission_profile: str | None = ":workspace",
    expose_aegis_tools: bool = True,
) -> CodexMigrationReport:
    report = CodexMigrationReport(dry_run=dry_run)
    codex_home = codex_home or Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    target = codex_home / "config.toml"
    report.target_path = target

    mcp_cfg = (aegis_config or {}).get("mcp") or {}
    aegis_servers = mcp_cfg.get("servers") or {}
    if not isinstance(aegis_servers, dict):
        report.errors.append("mcp.servers in AEGIS config is not a dict; cannot migrate.")
        return report

    translated: dict[str, dict[str, Any]] = {}
    for name, cfg in aegis_servers.items():
        out, skipped = _translate_one_server(str(name), cfg or {})
        if out is None:
            report.errors.append(
                f"server {name!r} skipped: {', '.join(skipped) or 'no transport configured'}"
            )
            continue
        translated[str(name)] = out
        report.migrated.append(str(name))
        if skipped:
            report.skipped_keys_per_server[str(name)] = skipped

    plugins: list[dict[str, Any]] = []
    plugin_query_succeeded = False
    if discover_plugins and not dry_run:
        plugins, err = _query_codex_plugins(codex_home=codex_home)
        if err:
            report.plugin_query_error = err
        else:
            plugin_query_succeeded = True
            report.migrated_plugins = [
                f"{plugin['name']}@{plugin['marketplace']}" for plugin in plugins
            ]

    if default_permission_profile:
        report.wrote_permissions_default = default_permission_profile

    if expose_aegis_tools:
        translated["aegis-tools"] = build_aegis_tools_mcp_entry()
        if "aegis-tools" not in report.migrated:
            report.migrated.append("aegis-tools")

    managed_block = render_codex_toml_section(
        translated,
        plugins=plugins,
        default_permission_profile=default_permission_profile,
    )

    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"could not read {target}: {exc}")
            return report
        without_managed = _strip_existing_managed_block(existing)
        if plugin_query_succeeded:
            without_managed = _strip_unmanaged_plugin_tables(without_managed)
        new_text = _insert_managed_block_at_top_level(without_managed, managed_block)
    else:
        new_text = managed_block

    if dry_run:
        return report

    try:
        codex_home.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".config.toml.", dir=str(codex_home))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(new_text)
            tmp_path.replace(target)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise
        report.written = True
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"could not write {target}: {exc}")
    return report


def maybe_migrate_from_metadata(metadata: dict[str, Any] | None) -> CodexMigrationReport | None:
    if not isinstance(metadata, dict):
        return None
    payload = metadata.get("_codex_runtime_migration")
    if not isinstance(payload, dict) or not payload.get("migrate_config", False):
        return None
    config = {"mcp": {"servers": payload.get("mcp_servers") or {}}}
    codex_home_raw = str(payload.get("codex_home") or "").strip()
    codex_home = Path(codex_home_raw).expanduser() if codex_home_raw else None
    profile = payload.get("default_permission_profile")
    if profile is not None:
        profile = str(profile)
    return migrate(
        config,
        codex_home=codex_home,
        discover_plugins=bool(payload.get("discover_plugins", False)),
        default_permission_profile=profile,
        expose_aegis_tools=bool(payload.get("expose_aegis_tools", True)),
    )
