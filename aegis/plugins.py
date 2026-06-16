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
import sys
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
ENTRY_POINT_GROUPS = ("hermes_agent.plugins", "aegis.plugins")
_VALID_PLUGIN_KINDS = {"standalone", "backend", "exclusive", "platform", "model-provider"}


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
    enabled: bool = True
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
            "enabled": self.enabled,
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
        manifest_version=int(data.get("manifest_version") or 1),
        requires_env=_list_field(data.get("requires_env") or data.get("required_env")),
        provides_tools=_string_list(data.get("provides_tools")),
        provides_hooks=_string_list(data.get("provides_hooks") or data.get("hooks")),
        enabled=_manifest_enabled(name, key or name, config),
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


def install(source: str, config, *, force: bool = False) -> str:
    src = Path(source).expanduser()
    base = _plugin_base()
    base.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise ValueError("plugin install currently expects a local .py file or directory")
    if src.is_file():
        dest = base / src.name
        if dest.exists() and not force:
            raise ValueError(f"{dest.name} already exists; pass --force to replace")
        shutil.copy2(src, dest)
        name = dest.stem
    else:
        dest = base / src.name
        if dest.exists():
            if not force:
                raise ValueError(f"{dest.name} already exists; pass --force to replace")
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        manifest = next((_read_manifest(dest / n, config, base=base, source="user") for n in MANIFEST_NAMES
                         if (dest / n).exists()), None)
        name = manifest.name if manifest else dest.name
    enable(name, config)
    clear_runtime_cache()
    return name


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
