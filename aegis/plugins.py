"""Drop-in plugin loader.

Place ``*.py`` files under ``~/.aegis/plugins/`` that define ``register(api)``.
The ``api`` object exposes ``register_tool(tool)``, ``register_channel(name, factory)``,
and ``register_provider(spec)`` so plugins extend AEGIS without core edits.

Example ``~/.aegis/plugins/hello_tool.py``::

    from aegis.tools.base import Tool, ToolResult
    class Hello(Tool):
        name = "hello"; description = "say hi"
        parameters = {"type": "object", "properties": {}}
        def run(self, args, ctx): return ToolResult.ok("hi!")
    def register(api): api.register_tool(Hello())
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata as importlib_metadata
import os
import json
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config as cfg


class PluginAPI:
    def __init__(self):
        self.tools: list = []
        self.channels: dict = {}
        self.providers: list[str] = []
        self.files: list[Path] = []
        self.errors: list[tuple[Path, str]] = []
        self._current_plugin: Path | None = None

    def register_tool(self, tool) -> None:
        tool.source = getattr(tool, "source", "") or "plugin"
        if self._current_plugin is not None:
            tool._aegis_plugin = str(self._current_plugin)
            _PLUGIN_TOOLS.setdefault(self._current_plugin, []).append(getattr(tool, "name", str(tool)))
        self.tools.append(tool)

    def register_channel(self, name: str, factory) -> None:
        self.channels[name] = factory
        if self._current_plugin is not None:
            _PLUGIN_CHANNELS.setdefault(self._current_plugin, []).append(name)

    def register_provider(self, spec) -> None:
        from .providers.registry import register_provider
        register_provider(spec)
        name = getattr(spec, "name", str(spec))
        self.providers.append(name)
        if self._current_plugin is not None:
            _PLUGIN_PROVIDERS.setdefault(self._current_plugin, []).append(name)

    def register_hook(self, event: str, fn) -> None:
        """Register an in-process Python hook.

        Events include 'on_session_start' (fn(agent)), 'pre_llm_call'
        (fn(messages, agent) -> messages|None to rewrite the request), and
        provider observers: 'pre_api_request', 'post_api_request',
        'api_request_error' (fn(payload, agent)).
        """
        _HOOKS.setdefault(event, []).append(fn)
        if self._current_plugin is not None:
            _PLUGIN_HOOKS.setdefault(self._current_plugin, []).append((event, fn))

    def register_middleware(self, kind: str, fn) -> None:
        """Register an in-process middleware wrapper.

        Kinds are ``tool_request``, ``tool_execution``, ``llm_request``, and
        ``llm_execution``. Middleware receives ``(payload, next_call, agent)``
        and may return a replacement payload/result. ``next_call`` is single-use
        so wrapper mistakes are caught close to the plugin that made them.
        """
        if kind not in _MIDDLEWARE_KINDS:
            raise ValueError(f"unknown middleware kind: {kind}")
        _MIDDLEWARE.setdefault(kind, []).append(fn)
        if self._current_plugin is not None:
            _PLUGIN_MIDDLEWARE.setdefault(self._current_plugin, []).append((kind, fn))

    def register_context_engine(self, name: str, engine_cls) -> None:
        """Register a custom context-management strategy (select via agent.context_engine)."""
        from .agent.context_engine import register
        register(name, engine_cls)


# Process-global hook registry, populated by plugins' register(api) at load time.
_HOOKS: dict[str, list] = {}
_MIDDLEWARE_KINDS = {"tool_request", "tool_execution", "llm_request", "llm_execution"}
_MIDDLEWARE: dict[str, list] = {}
_PLUGIN_HOOKS: dict[Path, list[tuple[str, Any]]] = {}
_PLUGIN_MIDDLEWARE: dict[Path, list[tuple[str, Any]]] = {}
_PLUGIN_PROVIDERS: dict[Path, list[str]] = {}
_PLUGIN_TOOLS: dict[Path, list[str]] = {}
_PLUGIN_CHANNELS: dict[Path, list[str]] = {}
_PLUGIN_MODULES: dict[Path, str] = {}


MANIFEST_NAMES = ("plugin.yaml", "plugin.yml", "aegis-plugin.json", "plugin.json")
INSTALL_METADATA_NAME = ".aegis-install.json"
ENTRY_POINT_GROUPS = ("hermes_agent.plugins", "aegis.plugins")
_VALID_PLUGIN_KINDS = {"standalone", "backend", "exclusive", "platform", "model-provider"}
_SUPPORTED_MANIFEST_VERSION = 1
_GITHUB_BROWSER_SEGMENTS = {
    "blob",
    "commit",
    "commits",
    "issues",
    "pull",
    "pulls",
    "releases",
    "tree",
    "wiki",
}


@dataclass
class PluginManifest:
    name: str
    path: Path
    entrypoint: Path | None
    entry_ref: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    kind: str = "standalone"
    key: str = ""
    category: str = ""
    source: str = "user"
    manifest_version: int = 1
    requires_env: list[Any] = field(default_factory=list)
    provides_tools: list[str] = field(default_factory=list)
    provides_hooks: list[str] = field(default_factory=list)
    provides_middleware: list[str] = field(default_factory=list)
    provides_channels: list[str] = field(default_factory=list)
    provides_providers: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    enabled: bool = True
    installed_from: str = ""
    install_url: str = ""
    install_subdir: str = ""
    trusted: bool = True
    install_metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "key": self.key or self.name,
            "path": str(self.path),
            "entrypoint": str(self.entrypoint) if self.entrypoint else "",
            "entry_ref": self.entry_ref,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "kind": self.kind,
            "category": self.category,
            "source": self.source,
            "manifest_version": self.manifest_version,
            "requires_env": self.requires_env,
            "provides_tools": self.provides_tools,
            "provides_hooks": self.provides_hooks,
            "provides_middleware": self.provides_middleware,
            "provides_channels": self.provides_channels,
            "provides_providers": self.provides_providers,
            "permissions": self.permissions,
            "enabled": self.enabled,
            "installed_from": self.installed_from,
            "install_url": self.install_url,
            "install_subdir": self.install_subdir,
            "trusted": self.trusted,
            "install_metadata": self.install_metadata,
        }


def fire_hook(event: str, *args, **kwargs):
    """Run every hook for an event. Returns the last non-None result so a rewrite hook can
    replace the value (e.g. modified messages); else None. Never raises."""
    result = None
    for fn in _HOOKS.get(event, []):
        try:
            out = fn(*args, **kwargs)
            if out is not None:
                result = out
        except Exception as e:  # noqa: BLE001 - a bad hook must not break the run
            from ._log import log_exc
            log_exc(f"plugin hook {event} failed: {e}")
    return result


def _call_middleware(fn, payload, next_call, agent):
    """Call middleware while tolerating older two-argument experiments."""

    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(payload, next_call, agent)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(payload, next_call, agent)
    argc = len(params)
    if argc >= 3:
        return fn(payload, next_call, agent)
    if argc == 2:
        return fn(payload, next_call)
    return fn(payload)


def fire_middleware(kind: str, payload: dict[str, Any], call_next, agent=None):
    """Run middleware wrappers for ``kind`` around ``call_next``.

    A broken middleware is logged and skipped by advancing to the next wrapper,
    preserving AEGIS' fail-soft plugin contract. If no middleware exists, this
    simply returns ``call_next(payload)``.
    """
    chain = list(_MIDDLEWARE.get(kind, []))
    if not chain:
        return call_next(payload)

    def invoke(index: int, current: dict[str, Any]):
        if index >= len(chain):
            return call_next(current)
        fn = chain[index]
        called = False

        def next_call(updated=None):
            nonlocal called
            if called:
                raise RuntimeError(f"middleware {kind} called next_call more than once")
            called = True
            return invoke(index + 1, current if updated is None else updated)

        try:
            result = _call_middleware(fn, current, next_call, agent)
            if result is None and not called:
                return next_call(current)
            return result
        except Exception as e:  # noqa: BLE001
            from ._log import log_exc
            log_exc(f"plugin middleware {kind} failed: {e}")
            if called:
                raise
            return invoke(index + 1, current)

    return invoke(0, payload)


def _plugin_base() -> Path:
    return cfg.sub("plugins")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def safe_mode_enabled() -> bool:
    return _env_truthy("AEGIS_SAFE_MODE") or _env_truthy("HERMES_SAFE_MODE")


def _contained_path(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_manifest_data(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _metadata_path(path: Path) -> Path:
    return path / INSTALL_METADATA_NAME


def _read_install_metadata(path: Path) -> dict[str, Any]:
    """Return install metadata for a plugin dir or any child below it."""

    base = _plugin_base().resolve()
    current = path if path.is_dir() else path.parent
    try:
        current = current.resolve()
    except OSError:
        return {}
    while True:
        meta_path = _metadata_path(current)
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
            return data if isinstance(data, dict) else {}
        if current == base:
            return {}
        try:
            current.relative_to(base)
        except ValueError:
            return {}
        parent = current.parent
        if parent == current:
            return {}
        current = parent


def _write_install_metadata(
    target: Path,
    *,
    source: str,
    installed_from: str,
    install_url: str = "",
    install_subdir: str = "",
    trusted: bool = True,
) -> None:
    data = {
        "source": source,
        "installed_from": installed_from,
        "install_url": install_url,
        "install_subdir": install_subdir,
        "trusted": bool(trusted),
    }
    _metadata_path(target).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _list_field(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _list_field(value) if str(item).strip()]


def _manifest_key(path: Path, name: str, base: Path | None) -> tuple[str, str]:
    if base is None:
        return name, ""
    try:
        rel_parent = path.parent.relative_to(base)
    except ValueError:
        return name, ""
    parts = [p for p in rel_parent.parts if p not in {"", "."}]
    if len(parts) >= 2:
        return "/".join(parts), parts[0]
    return name, ""


def _manifest_enabled(name: str, key: str, config=None) -> bool:
    disabled = set((config.get("plugins.disabled", []) if config else []) or [])
    allowlist = set((config.get("plugins.allowlist", []) if config else []) or [])
    aliases = {name, key or name}
    return not (aliases & disabled) and (not allowlist or bool(aliases & allowlist))


def _entrypoint_enabled(name: str, key: str, config=None) -> bool:
    disabled = set((config.get("plugins.disabled", []) if config else []) or [])
    enabled = set((config.get("plugins.enabled", []) if config else []) or [])
    allowlist = set((config.get("plugins.allowlist", []) if config else []) or [])
    aliases = {name, key or name}
    if aliases & disabled:
        return False
    if allowlist:
        return bool(aliases & allowlist)
    return bool(aliases & enabled)


def _entrypoint_groups() -> list[Any]:
    groups: list[Any] = []
    try:
        eps = importlib_metadata.entry_points()
        for group in ENTRY_POINT_GROUPS:
            if hasattr(eps, "select"):
                groups.extend(list(eps.select(group=group)))
            elif isinstance(eps, dict):
                groups.extend(list(eps.get(group, [])))
            else:
                groups.extend([ep for ep in eps if getattr(ep, "group", "") == group])
    except Exception:  # noqa: BLE001
        return []
    return groups


def _entrypoint_manifests(config=None) -> list[PluginManifest]:
    manifests: list[PluginManifest] = []
    seen: set[str] = set()
    for ep in _entrypoint_groups():
        name = str(getattr(ep, "name", "") or "").strip()
        value = str(getattr(ep, "value", "") or "").strip()
        if not name or not value or name in seen:
            continue
        seen.add(name)
        manifests.append(PluginManifest(
            name=name,
            key=name,
            path=Path(value),
            entrypoint=None,
            entry_ref=value,
            source="entrypoint",
            enabled=_entrypoint_enabled(name, name, config),
        ))
    return manifests


def _read_manifest(path: Path, config=None, *, base: Path | None = None,
                   source: str = "user") -> PluginManifest | None:
    data = _read_manifest_data(path)
    if not data:
        return None
    metadata = _read_install_metadata(path.parent)
    if metadata:
        source = str(metadata.get("source") or source)
    name = str(data.get("name") or path.parent.name)
    key = str(data.get("key") or "")
    category = str(data.get("category") or "")
    if not key:
        key, category = _manifest_key(path, name, base)
    elif not category and "/" in key:
        category = key.split("/", 1)[0]
    kind = str(data.get("kind") or data.get("type") or "standalone")
    if kind not in _VALID_PLUGIN_KINDS:
        kind = "standalone"
    entry = data.get("entrypoint") or data.get("main") or ""
    if not entry and path.suffix.lower() in {".yaml", ".yml"} and (path.parent / "__init__.py").exists():
        entry = "__init__.py"
    entrypoint = None
    if entry:
        root = path.parent.resolve()
        candidate = (path.parent / str(entry)).resolve()
        if _contained_path(root, candidate):
            entrypoint = candidate
    try:
        manifest_version = int(data.get("manifest_version") or 1)
    except (TypeError, ValueError):
        manifest_version = 1
    trusted = metadata.get("trusted")
    if trusted is None:
        trusted = source != "git"
    return PluginManifest(
        name=name,
        path=path,
        entrypoint=entrypoint,
        version=str(data.get("version") or ""),
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
        kind=kind,
        key=key or name,
        category=category,
        source=source,
        manifest_version=manifest_version,
        requires_env=_list_field(data.get("requires_env") or data.get("required_env")),
        provides_tools=_string_list(data.get("provides_tools")),
        provides_hooks=_string_list(data.get("provides_hooks") or data.get("hooks")),
        provides_middleware=_string_list(data.get("provides_middleware") or data.get("middleware")),
        provides_channels=_string_list(data.get("provides_channels") or data.get("channels")),
        provides_providers=_string_list(data.get("provides_providers") or data.get("providers")),
        permissions=_string_list(data.get("permissions")),
        enabled=_manifest_enabled(name, key or name, config),
        installed_from=str(metadata.get("installed_from") or ""),
        install_url=str(metadata.get("install_url") or ""),
        install_subdir=str(metadata.get("install_subdir") or ""),
        trusted=bool(trusted),
        install_metadata=metadata,
        raw=data,
    )


def list_manifests(config=None) -> list[PluginManifest]:
    if safe_mode_enabled():
        return []
    base = _plugin_base()
    found: list[PluginManifest] = []
    if not base.exists():
        return _entrypoint_manifests(config)
    seen: set[Path] = set()
    seen_manifest_dirs: set[Path] = set()
    for name in MANIFEST_NAMES:
        for path in sorted(base.rglob(name)):
            manifest_dir = path.parent.resolve()
            if manifest_dir in seen_manifest_dirs:
                continue
            manifest = _read_manifest(path, config, base=base, source="user")
            if manifest:
                seen_manifest_dirs.add(manifest_dir)
                found.append(manifest)
                if manifest.entrypoint:
                    seen.add(manifest.entrypoint)
    for path in sorted(base.glob("*.py")):
        if path in seen or path.name.startswith("_"):
            continue
        name = path.stem
        found.append(PluginManifest(
            name=name,
            key=name,
            path=path,
            entrypoint=path,
            source="user",
            enabled=_manifest_enabled(name, name, config),
        ))
    found.extend(_entrypoint_manifests(config))
    return found


def _clear_plugin_side_effects(path: Path) -> None:
    path = path.resolve()
    module_name = _PLUGIN_MODULES.pop(path, None)
    if module_name:
        sys.modules.pop(module_name, None)
    for event, fn in _PLUGIN_HOOKS.pop(path, []):
        hooks = _HOOKS.get(event, [])
        _HOOKS[event] = [h for h in hooks if h is not fn]
        if not _HOOKS[event]:
            _HOOKS.pop(event, None)
    for kind, fn in _PLUGIN_MIDDLEWARE.pop(path, []):
        chain = _MIDDLEWARE.get(kind, [])
        _MIDDLEWARE[kind] = [h for h in chain if h is not fn]
        if not _MIDDLEWARE[kind]:
            _MIDDLEWARE.pop(kind, None)
    names = _PLUGIN_PROVIDERS.pop(path, [])
    if names:
        try:
            from .providers.registry import unregister_provider
            for name in names:
                unregister_provider(name)
        except Exception:  # noqa: BLE001
            pass
    _PLUGIN_TOOLS.pop(path, None)
    _PLUGIN_CHANNELS.pop(path, None)


def _module_name_for(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"aegis_plugin_{path.stem}_{digest}"


def _manifest_identity(manifest: PluginManifest) -> Path | None:
    if manifest.entrypoint:
        return manifest.entrypoint.resolve()
    if manifest.entry_ref:
        safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", manifest.key or manifest.name)
        return Path(f"<entrypoint:{safe}>").resolve()
    return None


def _load_plugin_file(api: PluginAPI, path: Path, *, quiet: bool) -> None:
    path = path.resolve()
    api.files.append(path)
    _clear_plugin_side_effects(path)
    try:
        module_name = _module_name_for(path)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ValueError("could not load plugin module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        _PLUGIN_MODULES[path] = module_name
        if hasattr(module, "register"):
            api._current_plugin = path
            try:
                module.register(api)
            finally:
                api._current_plugin = None
    except Exception as e:  # noqa: BLE001
        api.errors.append((path, str(e)))
        if not quiet:
            print(f"  ! plugin {path.name} failed to load: {e}")


def _load_entry_ref(ref: str):
    module_name, sep, attr_path = ref.partition(":")
    if not module_name:
        raise ValueError("missing entry point module")
    obj: Any = importlib.import_module(module_name)
    if sep and attr_path:
        for part in attr_path.split("."):
            obj = getattr(obj, part)
    return obj


def _load_plugin_entrypoint(api: PluginAPI, manifest: PluginManifest, *, quiet: bool) -> None:
    identity = _manifest_identity(manifest)
    if identity is None:
        return
    api.files.append(identity)
    _clear_plugin_side_effects(identity)
    try:
        target = _load_entry_ref(manifest.entry_ref)
        register = getattr(target, "register", None)
        if callable(register):
            api._current_plugin = identity
            try:
                register(api)
            finally:
                api._current_plugin = None
            return
        if callable(target):
            api._current_plugin = identity
            try:
                target(api)
            finally:
                api._current_plugin = None
            return
        raise ValueError("entry point has no register(api) function")
    except Exception as e:  # noqa: BLE001
        api.errors.append((identity, str(e)))
        if not quiet:
            print(f"  ! plugin {manifest.name} failed to load: {e}")


def _manifest_matches(manifest: PluginManifest, name: str) -> bool:
    return name in {manifest.name, manifest.key or manifest.name}


def _find_manifest(name: str, config) -> PluginManifest | None:
    return next((m for m in list_manifests(config) if _manifest_matches(m, name)), None)


def _set_enabled(config, name: str, enabled: bool) -> None:
    plugins = config.data.setdefault("plugins", {})
    disabled = [x for x in plugins.get("disabled", []) if x != name]
    enabled_list = [x for x in plugins.get("enabled", []) if x != name]
    if enabled:
        enabled_list.append(name)
    else:
        disabled.append(name)
    plugins["enabled"] = sorted(dict.fromkeys(enabled_list))
    plugins["disabled"] = sorted(dict.fromkeys(disabled))
    config.save()


def enable(name: str, config) -> bool:
    manifest = _find_manifest(name, config)
    if manifest is None:
        return False
    _set_enabled(config, manifest.key or manifest.name, True)
    clear_runtime_cache()
    return True


def disable(name: str, config) -> bool:
    manifest = _find_manifest(name, config)
    if manifest is None:
        return False
    if manifest.entrypoint:
        _clear_plugin_side_effects(manifest.entrypoint)
    _set_enabled(config, manifest.key or manifest.name, False)
    clear_runtime_cache()
    return True


def _resolve_git_url(identifier: str) -> tuple[str, str | None]:
    value = str(identifier or "").strip()
    if not value:
        raise ValueError("plugin identifier is required")
    if _looks_like_local_path(value):
        raise ValueError("local plugin source does not exist")
    if value.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        if value.startswith("https://github.com/"):
            path = value[len("https://github.com/"):]
            path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
            parts = path.split("/")
            if len(parts) >= 3 and all(parts[:2]) and parts[2] in _GITHUB_BROWSER_SEGMENTS:
                repo = parts[1].removesuffix(".git")
                subdir = None
                if parts[2] == "tree" and len(parts) >= 5:
                    subdir = "/".join(p for p in parts[4:] if p).strip("/") or None
                return f"https://github.com/{parts[0]}/{repo}.git", subdir
        if "#" in value:
            git_url, _, fragment = value.partition("#")
            return git_url, (fragment.strip("/") or None)
        marker = ".git/"
        index = value.find(marker)
        if index != -1:
            git_url = value[: index + len(".git")]
            subdir = value[index + len(marker):].strip("/")
            return git_url, (subdir or None)
        return value, None

    parts = [p for p in value.strip("/").split("/") if p]
    if len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        subdir = "/".join(parts[2:]).strip("/")
        return f"https://github.com/{owner}/{repo}.git", (subdir or None)

    raise ValueError(
        f"Invalid plugin identifier: '{value}'. "
        "Use a local path, Git URL, or 'owner/repo' shorthand."
    )


def _looks_like_local_path(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return True
    if raw.startswith(("./", "../", "~", ".\\", "..\\")):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", raw))


def _repo_name_from_url(url: str) -> str:
    value = url.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    name = value.rsplit("/", 1)[-1]
    if ":" in name:
        name = name.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
    return name or "plugin"


def _safe_install_target(base: Path, name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "")).strip(".-")
    if not safe or safe in {".", ".."}:
        raise ValueError(f"invalid plugin name: {name!r}")
    target = (base / safe).resolve()
    root = base.resolve()
    if target == root or not _contained_path(root, target):
        raise ValueError(f"invalid plugin install target: {name!r}")
    return target


def _resolve_subdir_within(clone_root: Path, subdir: str) -> Path:
    root = clone_root.resolve()
    candidate = (clone_root / subdir).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"plugin subdirectory '{subdir}' escapes the repository")
    if not candidate.exists():
        raise ValueError(f"plugin subdirectory '{subdir}' does not exist in the repository")
    if not candidate.is_dir():
        raise ValueError(f"plugin subdirectory '{subdir}' is not a directory")
    return candidate


def _first_manifest(path: Path, config, *, base: Path, source: str) -> PluginManifest | None:
    return next(
        (
            _read_manifest(path / name, config, base=base, source=source)
            for name in MANIFEST_NAMES
            if (path / name).exists()
        ),
        None,
    )


def _manifest_data_for_install(plugin_dir: Path) -> dict[str, Any]:
    for name in MANIFEST_NAMES:
        data = _read_manifest_data(plugin_dir / name)
        if data:
            return data
    return {}


def _check_manifest_version(data: dict[str, Any], plugin_name: str) -> None:
    raw = data.get("manifest_version")
    if raw is None:
        return
    try:
        version = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"Plugin '{plugin_name}' has invalid manifest_version '{raw}' (expected an integer)."
        ) from None
    if version > _SUPPORTED_MANIFEST_VERSION:
        raise ValueError(
            f"Plugin '{plugin_name}' requires manifest_version {version}, "
            f"but this AEGIS build supports up to {_SUPPORTED_MANIFEST_VERSION}."
        )


def _copy_example_files(plugin_dir: Path) -> None:
    for example in plugin_dir.glob("*.example"):
        target = plugin_dir / example.stem
        if not target.exists():
            shutil.copy2(example, target)


def _missing_requires_env_names(manifest_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for item in _list_field(manifest_data.get("requires_env") or manifest_data.get("required_env")):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("key") or "").strip()
        else:
            name = str(item or "").strip()
        if name and not os.environ.get(name):
            missing.append(name)
    return sorted(dict.fromkeys(missing))


def _install_local(source: str, config, *, force: bool) -> dict[str, Any]:
    src = Path(source).expanduser()
    base = _plugin_base()
    base.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise ValueError("local plugin source does not exist")
    if src.is_file():
        if src.suffix != ".py":
            raise ValueError("plugin file must be a .py file")
        dest = base / src.name
        if dest.exists() and not force:
            raise ValueError(f"{dest.name} already exists; pass --force to replace")
        shutil.copy2(src, dest)
        name = dest.stem
        target = dest
        manifest_data: dict[str, Any] = {}
    else:
        dest = base / src.name
        if dest.exists():
            if not force:
                raise ValueError(f"{dest.name} already exists; pass --force to replace")
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        _write_install_metadata(
            dest,
            source="local",
            installed_from=str(src),
            trusted=True,
        )
        manifest = _first_manifest(dest, config, base=base, source="local")
        manifest_data = manifest.raw if manifest and manifest.raw else {}
        name = manifest.name if manifest else dest.name
        target = dest
    return {
        "ok": True,
        "plugin_name": name,
        "name": name,
        "target": str(target),
        "source": "local",
        "installed_from": str(src),
        "install_url": "",
        "install_subdir": "",
        "warnings": [],
        "missing_env": _missing_requires_env_names(manifest_data),
        "trusted": True,
    }


def _install_git(identifier: str, config, *, force: bool) -> dict[str, Any]:
    git_url, subdir = _resolve_git_url(identifier)
    base = _plugin_base()
    base.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if git_url.startswith(("http://", "file://")):
        warnings.append("Insecure URL scheme; prefer https:// or git@ for production installs.")

    git_exe = shutil.which("git") or "git"
    with tempfile.TemporaryDirectory(prefix="aegis-plugin-") as tmp:
        clone_root = Path(tmp) / "plugin"
        try:
            result = subprocess.run(
                [git_exe, "clone", "--depth", "1", git_url, str(clone_root)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("git is not installed or not in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git clone timed out after 120 seconds") from exc
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Git clone failed: {output or 'unknown error'}")

        plugin_dir = _resolve_subdir_within(clone_root, subdir) if subdir else clone_root
        manifest_data = _manifest_data_for_install(plugin_dir)
        plugin_name = str(
            manifest_data.get("name")
            or (subdir.rstrip("/").rsplit("/", 1)[-1] if subdir else _repo_name_from_url(git_url))
            or "plugin"
        )
        _check_manifest_version(manifest_data, plugin_name)
        target = _safe_install_target(base, plugin_name)
        if target.exists():
            if not force:
                raise ValueError(
                    f"Plugin '{plugin_name}' already exists. Pass --force to replace or update it."
                )
            shutil.rmtree(target)
        shutil.move(str(plugin_dir), str(target))

    if not any((target / name).exists() for name in MANIFEST_NAMES) and not (target / "__init__.py").exists():
        warnings.append(f"{plugin_name} has no plugin.yaml or __init__.py; it may not be a valid plugin.")
    _copy_example_files(target)
    _write_install_metadata(
        target,
        source="git",
        installed_from=identifier,
        install_url=git_url,
        install_subdir=subdir or "",
        trusted=False,
    )
    installed_manifest = _first_manifest(target, config, base=base, source="git")
    installed_name = installed_manifest.name if installed_manifest else plugin_name
    installed_data = installed_manifest.raw if installed_manifest and installed_manifest.raw else manifest_data
    return {
        "ok": True,
        "plugin_name": installed_name,
        "name": installed_name,
        "target": str(target),
        "source": "git",
        "installed_from": identifier,
        "install_url": git_url,
        "install_subdir": subdir or "",
        "warnings": warnings,
        "missing_env": _missing_requires_env_names(installed_data),
        "trusted": False,
    }


def install_details(source: str, config, *, force: bool = False, enable_now: bool = True) -> dict[str, Any]:
    raw_source = str(source or "")
    src = Path(raw_source).expanduser()
    if src.exists():
        result = _install_local(str(src), config, force=force)
    elif _looks_like_local_path(raw_source):
        raise ValueError("local plugin source does not exist")
    else:
        result = _install_git(raw_source, config, force=force)
    name = str(result.get("plugin_name") or result.get("name") or "")
    if enable_now:
        enable(name, config)
    else:
        disable(name, config)
    clear_runtime_cache()
    result["enabled"] = bool(enable_now)
    return result


def install(source: str, config, *, force: bool = False) -> str:
    result = install_details(source, config, force=force, enable_now=True)
    return str(result["plugin_name"])


def remove(name: str, config) -> bool:
    base = _plugin_base()
    manifests = list_manifests(config)
    match = next((m for m in manifests if _manifest_matches(m, name)), None)
    target = match.path if match else base / f"{name}.py"
    if target.is_file() and target.name in MANIFEST_NAMES:
        target = target.parent
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except FileNotFoundError:
        return False
    plugins = config.data.setdefault("plugins", {})
    aliases = {name}
    if match:
        aliases.update({match.name, match.key or match.name})
    plugins["enabled"] = [x for x in plugins.get("enabled", []) if x not in aliases]
    plugins["disabled"] = [x for x in plugins.get("disabled", []) if x not in aliases]
    config.save()
    if match and match.entrypoint:
        _clear_plugin_side_effects(match.entrypoint)
    clear_runtime_cache()
    return True


def clear_runtime_cache() -> None:
    """Clear process-local plugin side effects so the next load reflects config/files."""

    paths = set()
    paths.update(_PLUGIN_HOOKS)
    paths.update(_PLUGIN_MIDDLEWARE)
    paths.update(_PLUGIN_PROVIDERS)
    paths.update(_PLUGIN_TOOLS)
    paths.update(_PLUGIN_CHANNELS)
    paths.update(_PLUGIN_MODULES)
    for path in list(paths):
        _clear_plugin_side_effects(path)


def plugin_status(config=None, api: PluginAPI | None = None) -> list[dict[str, Any]]:
    api = api or load_plugins(quiet=True, config=config)
    errors = {Path(path).resolve(): msg for path, msg in api.errors}
    loaded = {Path(path).resolve() for path in api.files if Path(path).resolve() not in errors}
    rows: list[dict[str, Any]] = []
    for manifest in list_manifests(config):
        row = manifest.to_dict()
        entrypoint = _manifest_identity(manifest)
        if not manifest.enabled:
            status = "disabled"
        elif entrypoint is None:
            status = "inactive"
        elif entrypoint in errors:
            status = "error"
            row["error"] = errors[entrypoint]
        elif entrypoint in loaded:
            status = "loaded"
        else:
            status = "inactive"
        row.update({
            "status": status,
            "loaded": status == "loaded",
            "tool_names": sorted(_PLUGIN_TOOLS.get(entrypoint, [])) if entrypoint else [],
            "channel_names": sorted(_PLUGIN_CHANNELS.get(entrypoint, [])) if entrypoint else [],
            "provider_names": sorted(_PLUGIN_PROVIDERS.get(entrypoint, [])) if entrypoint else [],
            "hook_names": sorted(event for event, _fn in _PLUGIN_HOOKS.get(entrypoint, [])) if entrypoint else [],
            "middleware_kinds": sorted(kind for kind, _fn in _PLUGIN_MIDDLEWARE.get(entrypoint, [])) if entrypoint else [],
        })
        row["tools_registered"] = len(row["tool_names"])
        row["channels_registered"] = len(row["channel_names"])
        row["providers_registered"] = len(row["provider_names"])
        row["hooks_registered"] = len(row["hook_names"])
        row["middleware_registered"] = len(row["middleware_kinds"])
        rows.append(row)
    return rows


def load_plugins(*, quiet: bool = False, config=None) -> PluginAPI:
    api = PluginAPI()
    if safe_mode_enabled():
        return api
    base = _plugin_base()
    if config is None:
        try:
            from .config import Config
            config = Config.load()
        except Exception:  # noqa: BLE001
            config = None
    manifest_entries = list_manifests(config)
    handled: set[Path] = set()
    manifest_dirs = {m.path.parent for m in manifest_entries if m.path.name in MANIFEST_NAMES}
    for manifest in manifest_entries:
        if manifest.entry_ref:
            identity = _manifest_identity(manifest)
            if not manifest.enabled:
                if identity is not None:
                    _clear_plugin_side_effects(identity)
                continue
            _load_plugin_entrypoint(api, manifest, quiet=quiet)
            continue
        if not manifest.entrypoint:
            continue
        handled.add(manifest.entrypoint)
        if not manifest.enabled:
            _clear_plugin_side_effects(manifest.entrypoint)
            continue
        if manifest.entrypoint.exists():
            _load_plugin_file(api, manifest.entrypoint, quiet=quiet)
        else:
            api.errors.append((manifest.path, f"entrypoint not found: {manifest.entrypoint}"))
    if base.exists():
        for f in sorted(base.rglob("*.py")):
            if f.name.startswith("_") or f in handled:
                continue
            if any(f.is_relative_to(d) for d in manifest_dirs):
                continue
            if any(part.startswith(".") for part in f.relative_to(base).parts):
                continue
            _load_plugin_file(api, f, quiet=quiet)
    return api
