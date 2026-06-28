# Desktop Shell

AEGIS includes an Electron desktop shell in `desktop/` that launches and probes the same local dashboard backend used by `aegis ui`. The shell is intentionally thin: it owns native window lifecycle, backend startup, token handoff, crash/restart controls, and packaging scripts, while agent behavior stays in the shared Python runtime.

## Run from a checkout

```bash
aegis desktop

# or, for Electron development:
cd desktop
npm install
npm start
```

## Verify desktop contracts

```bash
cd desktop
npm install
npm run test:desktop
```

Release gates must not claim signed Windows installers, notarized macOS artifacts, or live auto-update behavior unless the matching runner and credentials are present. The maturity and live-QA matrices track these as manual OS-runner targets.

## Source map

- Desktop app source: `desktop/`
- Electron main process: `desktop/electron/main.js`
- Dashboard/backend contract: [developer-guide/dashboard-desktop-contracts.md](developer-guide/dashboard-desktop-contracts.md)
- Web dashboard operations: [dashboard.md](dashboard.md)
