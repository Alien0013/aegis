# Plugin LLM Access

Plugins extend AEGIS at the edge. They may expose tools, dashboard panels, webhooks, memory providers, or gateway adapters, but they should not bypass the central permission and redaction path.

## Contract

- Declare tool schemas and requirement checks explicitly.
- Route model calls through the configured provider registry instead of hardcoding keys.
- Store credentials in environment or secret storage, not plugin source.
- Redact errors before returning them to the agent context or dashboard.
- Keep plugin state under the active AEGIS home/profile.
- Add generated reference docs when a plugin exposes user-visible commands or APIs.

## Safety checks

A plugin must not echo API keys, dashboard tokens, OAuth tokens, provider responses containing secrets, or raw gateway payloads. If plugin output becomes tool context, treat it as untrusted data.
