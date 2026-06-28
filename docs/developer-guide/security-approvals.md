# Security Approvals

This AEGIS developer guide is part of the public docs maturity surface.

Security behavior must be explicit, testable, and consistent across CLI, dashboard, gateway, cron, and desktop surfaces.

## Contract

- Dangerous commands require approval unless the operator explicitly chooses a bypass mode.
- Secret redaction applies before logs or tool results enter model context.
- Sensitive file paths are protected by file safety policy.
- Dashboard auth tokens are minimized and never copied into generated docs.
- Gateway channels enforce allowlists, pairing, or channel authorization.
- Live QA records sanitized evidence without credentials.

A change that weakens these controls must include a deliberate config option, tests, and documentation.
