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
```

## Run from an AEGIS install
```bash
aegis desktop          # installs/updates ~/.aegis/desktop, then launches
aegis desktop --install-only
```

## Build installers
```bash
npm run dist         # → dist/  (.dmg on macOS, .exe on Windows, .AppImage/.deb on Linux)
```
Signed/notarized installers need each platform's signing certs and (usually)
that platform's machine or CI runner. The app itself just needs `aegis`
available — set `AEGIS_BIN` to override the executable path.

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
`main.js` → free port + random token → `spawn("aegis", ["dashboard","--port",P,"--no-open"])`
→ wait for `/` → `BrowserWindow.loadURL(http://127.0.0.1:P/?token=…)`. On quit it
stops the server. External links open in the system browser.
