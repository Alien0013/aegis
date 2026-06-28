# Dashboard and Desktop Contracts

This AEGIS developer guide is part of the public docs maturity surface.

The dashboard and desktop shells are thin surfaces over the same runtime. They should share backend contracts instead of reimplementing setup, model status, readiness, sessions, terminal, and gateway state independently.

## Contract

- FastAPI routes are explicit and appear in generated route references.
- WebSockets use token-minimized or ticket-based flows where possible.
- React state does not place long-lived credentials in URLs.
- Electron backend readiness is announced only after the API health route responds.
- Install, update, open, and uninstall proof is tracked separately per OS.
- Generated web bundles are reviewed before commit because hash churn can hide unrelated changes.
