# Environment Variables

Environment variables are for credentials, process-level overrides, and explicit live-smoke opt-ins. Behavioral settings should prefer config keys so dashboard, CLI, and docs can inspect them consistently.

Examples: provider API keys, gateway bot tokens, dashboard token/basic auth, live-test opt-ins such as `AEGIS_LIVE_TELEGRAM=1`, and CI/release variables.

Never commit credential values.
