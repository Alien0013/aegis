# Configuration

AEGIS configuration is YAML plus environment-backed credentials. Behavioral settings live in config; credential values live in the secret path or environment. Use `aegis config`, `aegis status`, and `aegis model doctor` to inspect current state.

Key areas: model/provider routing, fallback providers, auxiliary routing, gateway channels, dashboard auth, memory provider, skills, cron, tools, approvals, security, and display preferences.

Verification:

```bash
aegis status --json
aegis model doctor
aegis maturity --check
```
