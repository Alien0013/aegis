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
import json
import importlib.util
import shutil
import sys
from dataclasses import dataclass
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
        self.tools.append(tool)

    def register_channel(self, name: str, factory) -> None:
        self.channels[name] = factory

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
_PLUGIN_MODULES: dict[Path, str] = {}


MANIFEST_NAMES = ("aegis-plugin.json", "plugin.json")


@dataclass
class PluginManifest:
    name: str
    path: Path
    entrypoint: Path | None
    version: str = ""
    description: str = ""
    enabled: bool = True
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "entrypoint": str(self.entrypoint) if self.entrypoint else "",
            "version": self.version,
            "description": self.description,
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


def _contained_path(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_manifest(path: Path, config=None) -> PluginManifest | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or path.parent.name)
    entry = data.get("entrypoint") or data.get("main") or ""
    entrypoint = None
    if entry:
        root = path.parent.resolve()
        candidate = (path.parent / str(entry)).resolve()
        if _contained_path(root, candidate):
            entrypoint = candidate
    disabled = set((config.get("plugins.disabled", []) if config else []) or [])
    allowlist = set((config.get("plugins.allowlist", []) if config else []) or [])
    enabled = name not in disabled and (not allowlist or name in allowlist)
    return PluginManifest(
        name=name,
        path=path,
        entrypoint=entrypoint,
        version=str(data.get("version") or ""),
        description=str(data.get("description") or ""),
        enabled=enabled,
        raw=data,
    )


def list_manifests(config=None) -> list[PluginManifest]:
    base = _plugin_base()
    found: list[PluginManifest] = []
    if not base.exists():
        return found
    seen: set[Path] = set()
    for name in MANIFEST_NAMES:
        for path in sorted(base.rglob(name)):
            manifest = _read_manifest(path, config)
            if manifest:
                found.append(manifest)
                if manifest.entrypoint:
                    seen.add(manifest.entrypoint)
    disabled = set((config.get("plugins.disabled", []) if config else []) or [])
    allowlist = set((config.get("plugins.allowlist", []) if config else []) or [])
    for path in sorted(base.glob("*.py")):
        if path in seen or path.name.startswith("_"):
            continue
        name = path.stem
        found.append(PluginManifest(
            name=name,
            path=path,
            entrypoint=path,
            enabled=name not in disabled and (not allowlist or name in allowlist),
        ))
    return found


def _clear_plugin_side_effects(path: Path) -> None:
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


def _module_name_for(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"aegis_plugin_{path.stem}_{digest}"


def _load_plugin_file(api: PluginAPI, path: Path, *, quiet: bool) -> None:
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
    if not any(m.name == name for m in list_manifests(config)):
        return False
    _set_enabled(config, name, True)
    clear_runtime_cache()
    return True


def disable(name: str, config) -> bool:
    manifest = next((m for m in list_manifests(config) if m.name == name), None)
    if manifest is None:
        return False
    if manifest.entrypoint:
        _clear_plugin_side_effects(manifest.entrypoint)
    _set_enabled(config, name, False)
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
        manifest = next((_read_manifest(dest / n, config) for n in MANIFEST_NAMES
                         if (dest / n).exists()), None)
        name = manifest.name if manifest else dest.name
    enable(name, config)
    clear_runtime_cache()
    return name


def remove(name: str, config) -> bool:
    base = _plugin_base()
    manifests = list_manifests(config)
    match = next((m for m in manifests if m.name == name), None)
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
    plugins["enabled"] = [x for x in plugins.get("enabled", []) if x != name]
    plugins["disabled"] = [x for x in plugins.get("disabled", []) if x != name]
    config.save()
    if match and match.entrypoint:
        _clear_plugin_side_effects(match.entrypoint)
    clear_runtime_cache()
    return True


def clear_runtime_cache() -> None:
    """Clear process-local plugin side effects so the next load reflects config/files."""

    for path in list(_PLUGIN_HOOKS):
        _clear_plugin_side_effects(path)
    for path in list(_PLUGIN_PROVIDERS):
        _clear_plugin_side_effects(path)
    for path in list(_PLUGIN_MODULES):
        _clear_plugin_side_effects(path)


def load_plugins(*, quiet: bool = False, config=None) -> PluginAPI:
    api = PluginAPI()
    base = _plugin_base()
    if not base.exists():
        return api
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
    for f in sorted(base.rglob("*.py")):
        if f.name.startswith("_") or f in handled:
            continue
        if any(f.is_relative_to(d) for d in manifest_dirs):
            continue
        if any(part.startswith(".") for part in f.relative_to(base).parts):
            continue
        _load_plugin_file(api, f, quiet=quiet)
    return api
