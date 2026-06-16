# Plugins

AEGIS supports three plugin shapes:

- Drop a legacy `*.py` file in `~/.aegis/plugins/`.
- Install a manifest package with `plugin.json` or `aegis-plugin.json`.
- Install a Hermes-style package with `plugin.yaml` or `plugin.yml`.

## Manifest packages

```json
{
  "name": "hello",
  "version": "1.0.0",
  "description": "Adds a hello tool",
  "entrypoint": "main.py"
}
```

The entrypoint is a normal plugin module with `register(api)`:

```python
def register(api):
    api.register_tool(MyTool())
```

Hermes-style YAML packages are also accepted:

```yaml
name: langfuse
version: 1.0.0
description: Observability exporter
author: AEGIS
kind: backend
requires_env:
  - LANGFUSE_PUBLIC_KEY
provides_tools:
  - trace_export
hooks:
  - post_llm_call
```

If a YAML manifest omits `entrypoint`, AEGIS loads `__init__.py` when it exists.
Nested packages get stable category keys, for example
`~/.aegis/plugins/observability/langfuse/plugin.yaml` becomes
`observability/langfuse`. Enable and disable accept either the key or the
manifest name.

Plugins can also register runtime extension points:

```python
from aegis.providers.registry import ProviderSpec
from aegis.providers.base import ApiMode

def register(api):
    api.register_provider(ProviderSpec(
        name="local-lab",
        api_mode=ApiMode.CHAT_COMPLETIONS,
        base_url="http://localhost:8000/v1",
        default_model="local-model",
        context_length=64000,
        auth_scheme="none",
    ))
    api.register_channel("mychat", lambda: MyChatAdapter())
```

Provider plugins are loaded before provider resolution, so `model.provider:
local-lab` works without a built-in preset. Channel plugins are resolved by the
gateway adapter factory after built-ins, so `aegis gateway --channels mychat`
can start a plugin adapter. Disabling or removing a plugin clears its provider
and hook registrations from the current process before the next load.

## Commands

```bash
aegis plugins list
aegis plugins install ./hello-plugin
aegis plugins enable hello
aegis plugins disable hello
aegis plugins remove hello
aegis plugins doctor
```

Disable state is stored in `plugins.disabled`; `aegis plugins enable NAME`
removes a plugin from that disabled list without turning all other plugins off.
`plugins.enabled` records explicit enable operations for UI/history, but it is
not a global allowlist. To opt into strict loading, set `plugins.allowlist` to the
exact plugin names that may load. Legacy file plugins keep working; manifest
packages add lifecycle metadata and avoid loading disabled package files.

The dashboard `/api/plugins` endpoint and Plugins page show loaded files,
manifest state, registered tools, channel names, provider names, and load errors.
They also expose plugin status, source, kind, category, declared env/tool/hook
metadata, and per-plugin hook/middleware/provider/tool registrations.

Set `AEGIS_SAFE_MODE=1` or `HERMES_SAFE_MODE=1` to skip plugin discovery and
loading during troubleshooting.
