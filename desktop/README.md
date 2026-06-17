# AEGIS Desktop (Electron)

A native window around the AEGIS dashboard — double-click to run the whole
harness, no terminal needed. It spawns the local `aegis dashboard` server on a
random free port with a random token, then loads it.

## Run from source
```bash
cd desktop
npm install
npm start            # launches the app (requires `aegis` installed / on PATH)
npm run start:sandbox  # opt into Chromium's sandbox if your chrome-sandbox is configured
npm run test:desktop # backend-resolution helper tests
```

## Run from an AEGIS install
```bash
aegis desktop          # installs/updates ~/.aegis/desktop, then launches
aegis desktop --install-only
```

## Build installers
```bash
npm run dist         # → release/  (.dmg/.zip mac · nsis .exe win · .AppImage/.deb linux)
npm run dist:linux   # just the Linux targets
```
Branded with `build/icon.png` / `build/icon.ico` (generated from `assets/logo.svg`).
Signed/notarized installers need each platform's signing certs and (usually)
that platform's machine or CI runner. The app itself just needs `aegis`
available — set `AEGIS_BIN` to override the executable path.
Packaging uses `asar` and a `beforePack` cleanup hook that removes stale
`*-unpacked` build folders left by interrupted `electron-builder` runs.
Every package includes `build/install-stamp.json` with the git commit, app
version, Electron version, host platform, and target platform. Set
`AEGIS_RELEASE=1` (or `AEGIS_DESKTOP_RELEASE=1`) for a release build; the build
will fail early if the stamp is local/dirty, GitHub publishing lacks a token, or
Windows/macOS signing is disabled. For intentional internal builds, use the
explicit overrides named in the error message, such as
`AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE=1`.

On Windows, Explorer-launched GUI apps can inherit stale environment variables
from login time. The desktop backend resolver reads live user-scoped
`AEGIS_HOME` and `AEGIS_BIN` from `HKCU\Environment` before falling back to
`%LOCALAPPDATA%\aegis\venv\Scripts\aegis.exe` or `PATH`, so a value set with
`setx` works without logging out.

## Linux sandbox note
Electron's Chromium needs a root-owned setuid helper that `npm install` can't set,
so an unprivileged install would otherwise abort with a *"SUID sandbox helper …
is not configured correctly"* error. Since the app only loads our own localhost
dashboard, `npm start` launches Electron with `--no-sandbox` on Linux before
Chromium initializes; Linux packages also set the same executable argument. No
`sudo` needed. If your install *has* a correctly-configured sandbox and you'd
rather keep it for source runs, use `npm run start:sandbox` or
`AEGIS_ELECTRON_SANDBOX=1 npm start`.

## How it works
A structured Electron app under `electron/`:

- **`electron/main.js`** — the main process. Shows a splash instantly, starts the
  backend (`aegis dashboard` on a free port + random token), health-probes it
  while reporting progress to the splash, then opens the main window and swaps it
  in when loaded. Keeps the backend alive (**restart-on-crash**, up to 3×), stops
  it cleanly on quit, and captures stdout/stderr to a log for the failure screen.
- **`electron/backend-env.cjs`** / **`electron/windows-user-env.cjs`** — resolve
  the backend binary and launch environment, including the Windows registry
  fallback for stale GUI environments.
- **`scripts/before-pack.cjs`** — makes packaging idempotent after interrupted
  builds by clearing the stale unpacked output directory before staging.
- **`electron/boot.html`** — the splash / boot screen: branding, live progress,
  and an **error state** (Retry · Open logs · Quit) if the backend won't start.
- **`electron/preload.js`** — a locked-down `contextBridge` (the only thing the
  splash can call: boot status + retry/openLogs/quit).

Extras: single-instance lock, **window size/position persistence**, native menu
(`Cmd/Ctrl+1/2/3` → Home/Chat/Agents, `Cmd/Ctrl+,` → Settings, Restart Backend,
Open Logs), and external links open in the system browser. Logs live at
`<userData>/desktop.log`.
